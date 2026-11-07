import asyncio
import copy
import hashlib
import io
import json
import tarfile
import time

import aiohttp
import pytest

from profile_manager import ProfileManager
from proxy_manager import ProxyManager
from server import create_app
from test_suite import config,FakeSupervisor,start,login


def make_package(tmp_path,include_proxy=True):
    data=tmp_path/'source';profiles=ProfileManager(data,data/'profiles');nodes=ProxyManager(data)
    node=nodes.add_proxy('Proxy A','socks5h','node.example',1080,'user','private-test-password')
    profile=profiles.create_profile('Account A',node['id'])
    token=profiles.profile_dir(profile['id'])/'store/jetski-standalone-oauth-token';token.write_text('{"test":true}')
    file=tmp_path/('with-proxy.tar.gz' if include_proxy else 'without-proxy.tar.gz')
    profiles.export_profile(profile['id'],file,node if include_proxy else None)
    return file,node,profile


def test_export_only_includes_selected_bound_proxy(tmp_path):
    with_proxy,node,_=make_package(tmp_path,True)
    manager=ProfileManager(tmp_path/'source',tmp_path/'source/profiles')
    profile=manager.list_profiles()[0]
    without=tmp_path/'without.tar.gz';manager.export_profile(profile['id'],without)
    with tarfile.open(with_proxy) as archive:
        included=json.load(archive.extractfile('profile/proxy.json'))
        assert included['id']==node['id'] and included['password']=='private-test-password'
        assert len([m for m in archive if m.name.endswith('/proxy.json')])==1
    with tarfile.open(without) as archive:
        assert 'profile/proxy.json' not in archive.getnames()
    preview=manager.inspect_import(with_proxy)
    assert preview['has_proxy'] and preview['proxy']['name']=='Proxy A'
    assert 'password' not in preview['proxy'] and 'username' not in preview['proxy']
    assert manager.inspect_import(without)['proxy_message']=='无代理文件'


def test_preview_validates_without_extracting_all_profile_files(tmp_path,monkeypatch):
    package,_,_=make_package(tmp_path)
    data=tmp_path/'destination';manager=ProfileManager(data,data/'profiles')
    def cannot_extract(*args):raise AssertionError('Preview must not write all archive files')
    monkeypatch.setattr(manager,'_extract_import',cannot_extract)
    result=manager.inspect_import(package)
    assert result['has_proxy'] and not manager.list_profiles()
    assert list(manager.profiles_dir.iterdir())==[]


def test_optional_import_creates_bound_node_only_when_selected(tmp_path):
    package,node,_=make_package(tmp_path)
    data=tmp_path/'destination';profiles=ProfileManager(data,data/'profiles');nodes=ProxyManager(data)
    only_profile=profiles.import_profile(package,nodes,False)
    assert only_profile['proxy_id']=='' and nodes.list_proxies()==[]
    imported=profiles.import_profile(package,nodes,True)
    assert imported['proxy_id']==node['id']
    assert nodes.get_proxy(imported['proxy_id'])['password']==node['password']
    again=profiles.import_profile(package,nodes,True)
    assert again['proxy_id']==imported['proxy_id'] and len(nodes.list_proxies())==1


def test_proxy_id_collision_does_not_modify_existing_node_or_bindings(tmp_path):
    package,source_node,_=make_package(tmp_path)
    data=tmp_path/'destination';profiles=ProfileManager(data,data/'profiles');nodes=ProxyManager(data)
    existing=nodes.add_proxy('Existing','http','old.example',8080)
    existing['id']=source_node['id'];nodes._save()
    bound=profiles.create_profile('Existing account',existing['id'])
    original=copy.deepcopy(existing)
    imported=profiles.import_profile(package,nodes,True)
    assert imported['proxy_id']!=existing['id'] and existing==original
    assert bound['proxy_id']==existing['id'] and len(nodes.list_proxies())==2


def test_no_proxy_package_never_binds_stale_metadata_id(tmp_path):
    package,node,_=make_package(tmp_path,False)
    data=tmp_path/'destination';profiles=ProfileManager(data,data/'profiles');nodes=ProxyManager(data)
    nodes.import_proxy(node)
    imported=profiles.import_profile(package,nodes,True)
    assert imported['proxy_id']=='' and len(nodes.list_proxies())==1
    assert profiles.inspect_import(package)['has_proxy'] is False


def test_invalid_proxy_file_is_rejected_before_import_mutations(tmp_path):
    package,_,_=make_package(tmp_path)
    damaged=tmp_path/'invalid-proxy.tar.gz'
    with tarfile.open(package) as src,tarfile.open(damaged,'w:gz') as dest:
        for member in src:
            if member.name=='profile/proxy.json':
                content=json.dumps({'name':'Invalid','protocol':'http','host':'proxy.example','port':0}).encode()
                info=tarfile.TarInfo(member.name);info.size=len(content);dest.addfile(info,io.BytesIO(content))
            else:dest.addfile(member,src.extractfile(member) if member.isfile() else None)
    data=tmp_path/'destination';profiles=ProfileManager(data,data/'profiles');nodes=ProxyManager(data)
    with pytest.raises(ValueError):profiles.inspect_import(damaged)
    with pytest.raises(ValueError):profiles.import_profile(damaged,nodes,True)
    assert not profiles.list_profiles() and not nodes.list_proxies()


def test_failed_profile_import_rolls_back_new_proxy(tmp_path,monkeypatch):
    package,_,_=make_package(tmp_path)
    data=tmp_path/'destination';profiles=ProfileManager(data,data/'profiles');nodes=ProxyManager(data)
    def fail_profile(*args):raise OSError('simulated disk failure')
    monkeypatch.setattr(profiles,'create_profile',fail_profile)
    with pytest.raises(OSError):profiles.import_profile(package,nodes,True)
    assert not nodes.list_proxies() and not ProxyManager(data).list_proxies()
    assert not profiles.list_profiles()


def test_preview_confirm_and_export_apis_respect_optional_proxy(tmp_path):
    async def scenario():
        package,node,_=make_package(tmp_path)
        no_proxy,_,_=make_package(tmp_path/'no-proxy-source',False)
        app=create_app(config(tmp_path/'destination'),supervisor=FakeSupervisor());state=app['state']
        runner,port=await start(app);url='http://127.0.0.1:%d'%port
        async def upload(client,file):
            form=aiohttp.FormData();form.add_field('file',file.read_bytes(),filename=file.name,content_type='application/gzip')
            response=await client.post(url+'/__suite/api/profiles/import/preview',data=form)
            assert response.status==200
            return await response.json()
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                await login(client,url)
                preview=await upload(client,package)
                assert preview['has_proxy'] and 'password' not in preview['proxy']
                assert not state.proxies.list_proxies() and not state.profiles.list_profiles()
                response=await client.post(url+'/__suite/api/profiles/import/confirm',json={'import_token':preview['import_token'],'include_proxy':False})
                result=await response.json()
                assert response.status==200 and not result['proxy_imported'] and result['profile']['proxy_id']==''
                preview=await upload(client,package)
                response=await client.post(url+'/__suite/api/profiles/import/confirm',json={'import_token':preview['import_token'],'include_proxy':True})
                result=await response.json();imported=result['profile']
                assert result['proxy_imported'] and imported['proxy_id']==node['id']
                assert len(state.proxies.list_proxies())==1
                response=await client.get(url+'/__suite/api/profiles/export',params={'id':imported['id'],'include_proxy':'1'})
                assert response.status==200
                with tarfile.open(fileobj=io.BytesIO(await response.read()),mode='r:gz') as archive:
                    assert json.load(archive.extractfile('profile/proxy.json'))['id']==node['id']
                response=await client.get(url+'/__suite/api/profiles/export',params={'id':imported['id'],'include_proxy':'0'})
                with tarfile.open(fileobj=io.BytesIO(await response.read()),mode='r:gz') as archive:assert 'profile/proxy.json' not in archive.getnames()
                preview=await upload(client,no_proxy)
                assert preview['has_proxy'] is False and preview['proxy_message']=='无代理文件' and preview['proxy'] is None
                response=await client.post(url+'/__suite/api/profiles/import/confirm',json={'import_token':preview['import_token'],'include_proxy':True})
                result=await response.json();assert not result['proxy_imported'] and result['proxy_message']=='无代理文件' and result['profile']['proxy_id']==''
                assert not state.pending_imports
        finally:await runner.cleanup()
    asyncio.run(scenario())


def test_import_preview_is_session_bound_expires_and_can_be_cancelled(tmp_path):
    async def scenario():
        file,_,_=make_package(tmp_path)
        app=create_app(config(tmp_path/'destination'),supervisor=FakeSupervisor());state=app['state']
        runner,port=await start(app);url='http://127.0.0.1:%d'%port
        async def upload(client):
            form=aiohttp.FormData();form.add_field('file',file.read_bytes(),filename=file.name,content_type='application/gzip')
            return await (await client.post(url+'/__suite/api/profiles/import/preview',data=form)).json()
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                await login(client,url)
                preview=await upload(client);token=preview['import_token'];state.pending_imports[token]['session']='another-session'
                response=await client.post(url+'/__suite/api/profiles/import/confirm',json={'import_token':token,'include_proxy':True})
                assert response.status==400 and not state.proxies.list_proxies()
                state.pending_imports[token]['expires_at']=0
                response=await client.post(url+'/__suite/api/profiles/import/confirm',json={'import_token':token,'include_proxy':True})
                assert response.status==400 and token not in state.pending_imports
                preview=await upload(client);token=preview['import_token']
                response=await client.post(url+'/__suite/api/profiles/import/cancel',json={'import_token':token})
                assert response.status==200 and token not in state.pending_imports
                assert not state.profiles.list_profiles() and not state.proxies.list_proxies()
        finally:await runner.cleanup()
    asyncio.run(scenario())
