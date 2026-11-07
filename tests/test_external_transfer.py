import asyncio
import copy
import datetime
import json
from pathlib import Path

import aiohttp
import pytest

from config import atomic_json
from external_import import read_external,preview_external,import_external,export_external,native_token
from profile_manager import ProfileManager
from proxy_manager import ProxyManager
from server import create_app
from test_suite import config,FakeSupervisor,start,login


def cpa_record(**fields):
    # Synthetic fixture with the same shape as CPA; no user-supplied token copied.
    record=dict(access_token='dummy-access',refresh_token='dummy-refresh',type='antigravity',disabled=False,
        email='account@example.com',expired='2030-01-02T18:36:30+08:00',expires_in=3599,
        project_id='example-project',timestamp=1893576991000,
        antigravity_capability_hints=dict(endpoint='https://untrusted.example',model_ids=['dummy-model'],project_id='example-project'),
        proxy_url='socks5://dummy:private@proxy.example:1080')
    record.update(fields)
    return record


def write_json(folder,value,name='account.json'):
    folder.mkdir(parents=True,exist_ok=True)
    path=folder/name;path.write_text(json.dumps(value),encoding='utf-8');return path


def test_cpa_sample_shape_is_normalized_and_proxy_and_endpoint_ignored(tmp_path):
    file=write_json(tmp_path,cpa_record())
    preview=preview_external(file,'cpa')
    assert preview['account_count']==1 and not preview['has_proxy'] and preview['proxy'] is None
    assert 'dummy-access' not in json.dumps(preview) and 'dummy-refresh' not in json.dumps(preview)
    profiles=ProfileManager(tmp_path/'target',tmp_path/'target/profiles');nodes=ProxyManager(tmp_path/'target')
    existing=nodes.add_proxy('Existing','http','old.example',8080)
    old=copy.deepcopy(nodes.proxies)
    result=import_external(file,'cpa',profiles);profile=result['profile']
    assert profile['proxy_id']=='' and profile['email']=='account@example.com' and nodes.proxies==old
    token=json.loads((profiles.profile_dir(profile['id'])/'store/jetski-standalone-oauth-token').read_text())
    assert token['token']['expiry']=='2030-01-02T10:36:30Z' and token['token']['refresh_token']=='dummy-refresh'
    assert token['auth_method']=='consumer' and token['id_token_email']=='account@example.com'
    assert token['project_id']=='example-project'
    assert 'endpoint' not in str(token) and 'proxy_url' not in str(token) and 'model_ids' not in str(token)


def test_sub2api_mixed_bundle_imports_only_antigravity_accounts_without_proxies(tmp_path):
    bundle=dict(type='sub2api-data',version=1,exported_at='2030-01-01T00:00:00Z',
        proxies=[dict(proxy_key='p1',protocol='ss',password='ignore-this',host='ignored.example')],accounts=[
            dict(name='AG 1',platform='antigravity',type='oauth',proxy_key='p1',credentials=dict(access_token='dummy-a',refresh_token='dummy-r',expires_at='1893576990',email='one@example.com',project_id='p-one')),
            dict(name='AG 2',platform='antigravity',type='oauth',proxy_key='missing-node',credentials=dict(refresh_token='dummy-r2',email='two@example.com')),
            dict(name='Other',platform='openai',type='oauth',credentials=dict(refresh_token='ignored')),
            dict(name='API key',platform='antigravity',type='apikey',credentials=dict(api_key='ignored'))])
    file=write_json(tmp_path,bundle)
    preview=preview_external(file,'sub2api')
    assert preview['account_count']==2 and preview['skipped_count']==2 and not preview['has_proxy']
    profiles=ProfileManager(tmp_path/'target',tmp_path/'target/profiles')
    result=import_external(file,'sub2api',profiles)
    assert result['imported_count']==2 and all(not p['proxy_id'] for p in result['profiles'])
    assert [p['name'] for p in result['profiles']]==['AG 1','AG 2']
    assert not (tmp_path/'target/proxies.json').exists()


def test_expired_and_refresh_only_credentials_are_not_marked_fresh():
    value,warning=native_token(cpa_record(expired='2020-01-01T00:00:00Z'),'x@example.com')
    assert value['token']['expiry']=='2020-01-01T00:00:00Z' and warning
    value,warning=native_token(dict(refresh_token='dummy-refresh',expires_in=3600),'x@example.com')
    assert value['token']['access_token']=='' and value['token']['expiry'].startswith('1970-') and warning
    value,warning=native_token(dict(access_token='dummy',refresh_token='dummy-r',timestamp=1893456000000,expires_in=3600),'x@example.com')
    assert value['token']['expiry']=='2030-01-01T01:00:00Z'


@pytest.mark.parametrize('record,message',[
    (cpa_record(type='codex'),'没有可导入'),
    (cpa_record(disabled=True),'没有可导入'),
    (cpa_record(refresh_token=''),'refresh_token'),
    (cpa_record(expired='bad-date'),'时间格式'),
    (cpa_record(expires_in=-1),'expires_in'),
    (dict(type='antigravity',refresh_token=['bad']),'格式无效'),
])
def test_bad_cpa_files_do_not_create_partial_accounts(tmp_path,record,message):
    file=write_json(tmp_path,record);profiles=ProfileManager(tmp_path/'target',tmp_path/'target/profiles')
    with pytest.raises(ValueError,match=message):import_external(file,'cpa',profiles)
    assert not profiles.list_profiles()


def test_all_accounts_validated_before_creating_any_profile_and_rollback_on_failure(tmp_path,monkeypatch):
    file=write_json(tmp_path,[cpa_record(name='First'),cpa_record(name='Bad',refresh_token='')])
    profiles=ProfileManager(tmp_path/'target',tmp_path/'target/profiles')
    with pytest.raises(ValueError):import_external(file,'cpa',profiles)
    assert not profiles.list_profiles()
    file=write_json(tmp_path,[cpa_record(name='First'),cpa_record(name='Second')])
    import external_import
    real=external_import.atomic_json;count=0
    def fail(path,value):
        nonlocal count
        count+=1
        if count==2:raise OSError('simulated disk failure')
        real(path,value)
    monkeypatch.setattr(external_import,'atomic_json',fail)
    with pytest.raises(OSError):import_external(file,'cpa',profiles)
    assert not profiles.list_profiles() and not any(p.is_dir() and len(p.name)==32 for p in profiles.profiles_dir.iterdir())


def test_cpa_and_sub2api_export_roundtrip_account_only(tmp_path):
    source=write_json(tmp_path,cpa_record(name='My Account'))
    profiles=ProfileManager(tmp_path/'target',tmp_path/'target/profiles')
    profile=import_external(source,'cpa',profiles)['profile']
    profile['proxy_id']='dummy-existing-node';profiles._save()
    for format in ('cpa','sub2api'):
        value=export_external(profiles,profile['id'],format)
        encoded=json.dumps(value)
        assert 'proxy_url' not in encoded and 'proxy_key' not in encoded and 'dummy-existing-node' not in encoded
        if format=='cpa':
            assert value['type']=='antigravity' and value['expired']=='2030-01-02T10:36:30Z'
            assert value['project_id']=='example-project'
        else:
            assert value['type']=='sub2api-data' and value['version']==1 and value['proxies']==[]
            assert value['accounts'][0]['platform']=='antigravity' and value['accounts'][0]['type']=='oauth'
            assert value['accounts'][0]['credentials']['expires_at']=='1893580590'
        file=write_json(tmp_path,value,name=format+'.json')
        result=import_external(file,format,profiles)
        assert result['profile']['proxy_id']=='' and result['profile']['id']!=profile['id']
        token=json.loads((profiles.profile_dir(result['profile']['id'])/'store/jetski-standalone-oauth-token').read_text())
        assert token['token']['refresh_token']=='dummy-refresh' and token['project_id']=='example-project'


def test_external_export_uses_current_home_and_missing_or_business_token_is_rejected(tmp_path):
    profiles=ProfileManager(tmp_path/'target',tmp_path/'target/profiles')
    profile=profiles.create_profile('Blank')
    with pytest.raises(ValueError,match='凭据'):export_external(profiles,profile['id'],'cpa')
    value,_=native_token(cpa_record(),'home@example.com')
    atomic_json(profiles.profile_dir(profile['id'])/'store/jetski-standalone-oauth-token',value)
    home=profiles.home_for(profile['id']);home.mkdir(parents=True);(home/'.suite-home-initialized').write_text('1')
    fresh=copy.deepcopy(value);fresh['token']['refresh_token']='fresh-home';atomic_json(home/'.gemini/jetski-standalone-oauth-token',fresh)
    assert export_external(profiles,profile['id'],'cpa')['refresh_token']=='fresh-home'
    fresh['auth_method']='enterprise';atomic_json(home/'.gemini/jetski-standalone-oauth-token',fresh)
    with pytest.raises(ValueError,match='授权类型'):export_external(profiles,profile['id'],'sub2api')


def test_external_import_export_apis_convert_ignore_proxy_and_detect(tmp_path):
    async def scenario():
        app=create_app(config(tmp_path/'target'),supervisor=FakeSupervisor());state=app['state'];detected=[]
        state.request_detection=lambda p:detected.append(p)
        old=state.profiles.create_profile('Existing');node=state.proxies.add_proxy('Existing node','http','old.example',8080)
        old['proxy_id']=node['id'];state.profiles._save();old_ids=[old['id']];old_nodes=copy.deepcopy(state.proxies.proxies)
        runner,port=await start(app);url='http://127.0.0.1:%d'%port
        async def upload(client,value,source):
            form=aiohttp.FormData();form.add_field('file',json.dumps(value).encode(),filename=source+'.json',content_type='application/json')
            response=await client.post(url+'/__suite/api/profiles/import/preview',params={'source':source},data=form)
            assert response.status==200
            return await response.json()
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                await login(client,url)
                preview=await upload(client,cpa_record(),'cpa')
                assert preview['source']=='cpa' and not preview['has_proxy'] and 'dummy-refresh' not in json.dumps(preview)
                response=await client.post(url+'/__suite/api/profiles/import/confirm',json=dict(import_token=preview['import_token'],include_proxy=True))
                result=await response.json();assert response.status==200
                p=result['profile'];assert not p['proxy_id'] and not result['proxy_imported'] and p['id'] in detected
                assert state.proxies.proxies==old_nodes and state.profiles.get_profile(old_ids[0])['proxy_id']==node['id']
                for format in ('cpa','sub2api'):
                    response=await client.get(url+'/__suite/api/profiles/export',params=dict(id=p['id'],format=format,include_proxy='1'))
                    assert response.status==200 and response.headers['Content-Type'].startswith('application/json')
                    assert '.json' in response.headers['Content-Disposition'] and response.headers['Cache-Control']=='no-store'
                    value=await response.json()
                    assert 'proxy_key' not in json.dumps(value)
                    if format=='sub2api':assert value['proxies']==[]
                sub=dict(type='sub2api-data',version=1,proxies='ignored regardless of format',accounts=[
                    dict(name='Sub account',platform='antigravity',type='oauth',credentials=dict(refresh_token='dummy-r',access_token='dummy-a',expires_at=1893580590),proxy_key='ignore')])
                preview=await upload(client,sub,'sub2api')
                assert preview['account_count']==1 and not preview['has_proxy']
                response=await client.post(url+'/__suite/api/profiles/import/confirm',json=dict(import_token=preview['import_token'],include_proxy=False))
                assert response.status==200 and (await response.json())['profile']['proxy_id']==''
                assert len(detected)==2 and state.proxies.proxies==old_nodes
                assert state.profiles.get_profile(old['id'])['id']==old['id']
        finally:await runner.cleanup()
    asyncio.run(scenario())
