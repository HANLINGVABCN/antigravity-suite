import asyncio
import copy
import shutil

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
                assert 'link' not in detail['proxy']
                response=await client.get(url+'/__suite/api/proxies/detail',params={'id':original_id,'include_link':'1'})
                linked=(await response.json())['proxy']
                assert linked['link']=='http://user:private-test-password@old.example:8080#N'
                assert response.headers['Cache-Control']=='no-store'
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


def test_saved_node_link_is_importable_with_ipv6_and_encoded_credentials(tmp_path):
    nodes=ProxyManager(tmp_path)
    node=nodes.add_proxy('节点 A#1','socks5h','2001:db8::1',1080,'a@b','p:@ /')
    link=nodes.get_proxy_link(node)
    parsed=nodes.parse_link(link)
    assert link.startswith('socks5h://a%40b:p%3A%40%20%2F@[2001:db8::1]:1080#')
    assert all(parsed[key]==node[key] for key in ('name','protocol','host','port','username','password'))


@pytest.mark.skipif(shutil.which('node') is None,reason='Frontend helper test requires Node.js; Python/API tests still run')
def test_editor_submits_both_representations_for_server_validation():
    import subprocess,json
    from pathlib import Path
    editor=Path(__file__).parents[1]/'static/proxy-editor.js'
    script=r'''
const {buildUpdate} = require(process.argv[1]);
const original = 'http://user:old-password@old.example:8080#Original';
const fields = {name:'Renamed', host:'new.example',port:443,protocol:'https',username:'new',password:'new-password',clear_password:false};
const manual = buildUpdate('stable-id', original, original, fields);
const replaced = buildUpdate('stable-id', original, 'socks5://other.example:1080#New', fields);
const cleared = buildUpdate('stable-id', original, '', {...fields,password:'',clear_password:true});
process.stdout.write(JSON.stringify({manual,replaced,cleared}));
'''
    result=subprocess.run(['node','-e',script,str(editor)],capture_output=True,text=True,check=True)
    values=json.loads(result.stdout)
    assert values['manual']['id']=='stable-id' and values['manual']['name']=='Renamed' and values['manual']['host']=='new.example'
    assert values['manual']['link']=='http://user:old-password@old.example:8080#Original' and values['manual']['password']=='new-password'
    assert values['replaced']['link']=='socks5://other.example:1080#New' and values['replaced']['host']=='new.example'
    assert values['cleared']['clear_password'] and 'link' not in values['cleared']


def test_link_and_fields_must_match_before_node_is_saved(tmp_path):
    nodes=ProxyManager(tmp_path)
    node=nodes.add_proxy('N','http','node.example',8080,'user','private-password')
    original=copy.deepcopy(node)
    link=nodes.get_proxy_link(node)
    fields=dict(name='N',protocol='http',host='node.example',port='8080',username='user',password='',clear_password=False,link=link)
    nodes.update_proxy(node['id'],fields)
    assert node['id']==original['id'] and node['password']=='private-password'
    disk=nodes.file_path
    saved_disk=__import__('pathlib').Path(disk).read_bytes()
    for key,value in [('name','Different'),('protocol','socks5'),('host','other.example'),('port',8888),('username','other'),('password','a-new-secret'),('clear_password',True)]:
        mismatch=dict(fields,**{key:value})
        before=copy.deepcopy(node)
        with pytest.raises(ValueError,match='保存失败.*不一致') as error:nodes.update_proxy(node['id'],mismatch)
        assert node==before and __import__('pathlib').Path(disk).read_bytes()==saved_disk
        assert 'private-password' not in str(error.value) and 'a-new-secret' not in str(error.value)
    nodes.update_proxy(node['id'],dict(fields,name='New',host='other.example',password='changed',link='http://user:changed@other.example:8080#New'))
    assert node['id']==original['id'] and node['created_at']==original['created_at'] and node['host']=='other.example'


def test_creation_rejects_conflicting_link_and_accepts_matching_fields(tmp_path):
    nodes=ProxyManager(tmp_path)
    with pytest.raises(ValueError,match='不一致'):
        nodes.add_from_input(dict(link='socks5://proxy.example:1080#N',name='N',protocol='http',host='proxy.example',port=1080))
    assert not nodes.list_proxies()
    node=nodes.add_from_input(dict(link='socks5://proxy.example:1080#N',name='N',protocol='socks5',host='proxy.example',port='1080'))
    assert len(nodes.list_proxies())==1 and node['protocol']=='socks5'
    original=copy.deepcopy(node)
    with pytest.raises(ValueError):nodes.add_from_input(dict(link='http://one:80\nhttp://two:80',host='one'))
    assert nodes.list_proxies()==[original]


def test_link_conflict_api_reports_failure_without_changing_ids_or_bindings(tmp_path):
    async def scenario():
        app=create_app(config(tmp_path),supervisor=FakeSupervisor());state=app['state']
        node=state.proxies.add_proxy('N','http','node.example',8080,'user','old')
        profile=state.profiles.create_profile('P',node['id'])
        original=copy.deepcopy(node)
        runner,port=await start(app);url='http://127.0.0.1:%d'%port
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                await login(client,url)
                response=await client.post(url+'/__suite/api/proxies/update',json={'id':node['id'],'link':state.proxies.get_proxy_link(node),'host':'conflict.example'})
                result=await response.json()
                assert response.status==400 and not result['ok'] and '地址' in result['error'] and '保存失败' in result['error']
                assert node==original and profile['proxy_id']==node['id']
                response=await client.post(url+'/__suite/api/proxies/update',json={'id':node['id'],'link':'http://user:old@new.example:8080#N','host':'new.example','password':''})
                assert response.status==200 and (await response.json())['proxy']['id']==original['id']
                response=await client.post(url+'/__suite/api/proxies',json={'link':'http://one.example:80#N','host':'another.example'})
                assert response.status==400 and len(state.proxies.list_proxies())==1
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
