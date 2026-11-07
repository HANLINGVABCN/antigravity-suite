import asyncio
import copy

import aiohttp
import pytest

from profile_manager import ProfileManager
from proxy_manager import ProxyManager
from server import create_app
from test_suite import config, FakeSupervisor, FakeProcess, start, login


def test_node_edits_preserve_identity_bound_profiles_and_persist_after_reload(tmp_path):
    nodes=ProxyManager(tmp_path)
    node=nodes.add_proxy('original','http','old.example',8080,'user','old-password')
    original_id=node['id'];created=node['created_at']
    profiles=ProfileManager(tmp_path,tmp_path/'profiles')
    a=profiles.create_profile('A',original_id);b=profiles.create_profile('B',original_id)
    nodes.update_proxy(original_id,dict(name='renamed',protocol='https',host='2001:db8::1',port=443,username='new-user',password='new-password'))
    assert node['id']==original_id and node['created_at']==created
    assert node['host']=='2001:db8::1' and len(nodes.proxies)==1
    nodes.update_proxy(original_id,dict(link='socks5h://other:p%40ss@new.example:1080#FromLink'))
    assert node['id']==original_id and node['created_at']==created and node['password']=='p@ss'
    reloaded=ProxyManager(tmp_path).get_proxy(original_id)
    assert reloaded==node and reloaded['protocol']=='socks5h'
    assert [p['proxy_id'] for p in profiles.list_profiles()]==[original_id,original_id]
    profiles.update_profile(a['id'],name='A renamed',email='dummy@example.com',proxy_id='')
    assert a['id']!=b['id'] and a['id']==profiles.list_profiles()[0]['id']
    assert profiles.profile_dir(a['id']).name==a['id']
    assert ProfileManager(tmp_path,tmp_path/'profiles').get_profile(a['id'])['id']==a['id']


def test_empty_password_preserves_explicit_clear_removes_and_invalid_edit_is_atomic(tmp_path,monkeypatch):
    nodes=ProxyManager(tmp_path)
    node=nodes.add_proxy('N','http','node.example',8080,'user','password ')
    nodes.update_proxy(node['id'],dict(name='Changed',password=''))
    assert node['password']=='password '
    original=copy.deepcopy(node)
    for fields in [dict(name='bad',port=0),dict(id='replacement'),dict(created_at='fake'),dict(host='bad\nHost'),dict(link='http://valid:80\nsocks5://another:1080')]:
        with pytest.raises(ValueError):nodes.update_proxy(node['id'],fields)
        assert node==original
    def fail_save():raise OSError('intentional storage failure')
    with monkeypatch.context() as patch:
        patch.setattr(nodes,'_save',fail_save)
        with pytest.raises(OSError):nodes.update_proxy(node['id'],dict(name='not-saved'))
        assert node==original
    nodes.update_proxy(node['id'],dict(clear_password=True,password=''))
    assert node['password']=='' and node['id']==original['id']


def test_node_detail_and_update_api_keep_ids_and_password_private_by_default(tmp_path):
    async def scenario():
        app=create_app(config(tmp_path),supervisor=FakeSupervisor());state=app['state']
        node=state.proxies.add_proxy('N','http','old.example',8080,'user','private-test-password')
        profile=state.profiles.create_profile('A',node['id'])
        original_id=node['id'];profile_id=profile['id'];created=profile['created_at']
        runner,port=await start(app);url='http://127.0.0.1:%d'%port
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                response=await client.get(url+'/__suite/api/proxies/detail',params={'id':original_id,'reveal_password':'1'})
                assert response.status==401
                await login(client,url)
                detail=await (await client.get(url+'/__suite/api/proxies/detail',params={'id':original_id})).json()
                assert detail['proxy']['id']==original_id and detail['proxy']['has_password']
                assert 'password' not in detail['proxy'] and detail['bound_profiles']==[dict(id=profile_id,name='A')]
                response=await client.get(url+'/__suite/api/proxies/detail',params={'id':original_id,'reveal_password':'1'})
                assert (await response.json())['proxy']['password']=='private-test-password'
                assert response.headers['Cache-Control']=='no-store'
                response=await client.post(url+'/__suite/api/proxies/update',json={'id':original_id,'name':'N renamed','host':'new.example','password':''})
                assert response.status==200
                result=await response.json()
                assert result['proxy']['id']==original_id and 'password' not in result['proxy']
                assert state.proxies.get_proxy(original_id)['password']=='private-test-password'
                assert state.profiles.get_profile(profile_id)['proxy_id']==original_id
                response=await client.post(url+'/__suite/api/profiles/update',json={'id':profile_id,'name':'A renamed','email':'example@example.com','proxy_id':original_id})
                assert response.status==200
                saved=(await response.json())['profile'];assert saved['id']==profile_id and saved['created_at']==created
                response=await client.post(url+'/__suite/api/proxies/update',json={'id':original_id,'new_id':'replacement'})
                assert response.status==400
                response=await client.post(url+'/__suite/api/profiles/update',json={'id':profile_id,'new_id':'replacement'})
                assert response.status==400
                response=await client.post(url+'/__suite/api/proxies/update',json={'id':original_id,'link':'socks5://another.example:1080'})
                assert response.status==200 and (await response.json())['proxy']['id']==original_id
                assert len(state.proxies.list_proxies())==1
        finally:await runner.cleanup()
    asyncio.run(scenario())


def test_active_node_connection_requires_stop_but_rename_does_not(tmp_path):
    async def scenario():
        supervisor=FakeSupervisor();app=create_app(config(tmp_path),supervisor=supervisor);state=app['state']
        node=state.proxies.add_proxy('N','http','node.example',8080)
        profile=state.profiles.create_profile('A',node['id']);state.active=profile['id']
        supervisor.process=FakeProcess();supervisor.active_port=45000
        runner,port=await start(app);url='http://127.0.0.1:%d'%port
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                await login(client,url)
                response=await client.post(url+'/__suite/api/proxies/update',json={'id':node['id'],'name':'new label'})
                assert response.status==200 and node['name']=='new label'
                response=await client.post(url+'/__suite/api/proxies/update',json={'id':node['id'],'host':'other.example'})
                assert response.status==409 and (await response.json())['requires_stop']
                assert node['host']=='node.example' and profile['proxy_id']==node['id']
                await client.post(url+'/__suite/api/system/stop')
                response=await client.post(url+'/__suite/api/proxies/update',json={'id':node['id'],'host':'other.example'})
                assert response.status==200 and node['host']=='other.example' and profile['proxy_id']==node['id']
        finally:await runner.cleanup()
    asyncio.run(scenario())
