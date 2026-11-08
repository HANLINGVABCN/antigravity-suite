import asyncio
import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

from config import atomic_json
from desktop_guard import encode_archive,read_archive,install,restore,MARKER
from login_sessions import LoginSessions
from logout_protection import transform_client,blocks_logout,WARNING
from test_native_account_sessions import setup
from test_suite import start,login,config,FakeSupervisor
from server import create_app

FIXTURE='''globalThis.AuthFixture=class {
async loginWithRedirect(a){let g=await this._lsClient.login({});var e=g.authResult;if(e?.hasValidAuth)this.result=e;}
async logout(){this.isLoggingOut=true;try{await this._lsClient.authLogout({});}finally{this.result=null;}}
};'''


@pytest.mark.skipif(os.name=='nt',reason='Full runtime restore uses Linux shared-history symlinks')
def test_explicit_same_account_login_is_new_but_renewal_and_restore_are_not(tmp_path):
    async def scenario():
        state,a,home=setup(tmp_path);manager=state.profiles
        await state.sync_native_login(force=True)
        old=(manager.profile_dir(a['id'])/'store/jetski-standalone-oauth-token').read_bytes()
        nonce=state.session_ops.begin(source='native')
        atomic_json(home/'.gemini/jetski-standalone-oauth-token',dict(token=dict(refresh_token='second-A'),id_token_email='a@example.invalid'))
        await state.sync_native_login(force=True)
        assert len(manager.list_profiles())==1
        identifier=await state.session_ops.complete(nonce,True)
        assert identifier!=a['id'] and len(manager.list_profiles())==2
        assert (manager.profile_dir(a['id'])/'store/jetski-standalone-oauth-token').read_bytes()==old
        assert await state.session_ops.complete(nonce,True)==identifier
        atomic_json(home/'.gemini/jetski-standalone-oauth-token',dict(token=dict(refresh_token='second-A',access_token='renewed'),id_token_email='a@example.invalid'))
        await state.sync_native_login(force=True)
        assert state.active==identifier and len(manager.list_profiles())==2
        state.native_runtime_proxy_id=None;manager.get_profile(a['id'])['proxy_id']=''
        await state.activate(a['id']);await state.sync_native_login(force=True)
        assert state.active==a['id'] and len(manager.list_profiles())==2
        if state.outbound_bridge:await state.outbound_bridge.close()
    asyncio.run(scenario())


def test_successful_same_token_login_and_persisted_intent_are_distinct(tmp_path):
    sessions=LoginSessions(tmp_path);token=dict(fingerprint='long',full_fingerprint='full')
    sessions.restored('old',token);nonce=sessions.begin('native',token)
    sessions=LoginSessions(tmp_path);sessions.complete(nonce,True)
    assert sessions.new_login(token,'old',True)==nonce
    sessions.captured(nonce,nonce,token)
    assert sessions.complete(nonce,True)==nonce and sessions.new_login(token,nonce,True) is None
    failed=sessions.begin('native',token);sessions.complete(failed,False)
    assert sessions.new_login(token,nonce,True) is None
    with pytest.raises(ValueError):sessions.complete({},True)


def test_unfinished_login_cannot_overwrite_previous_session_on_service_stop(tmp_path):
    async def scenario():
        state,a,home=setup(tmp_path);manager=state.profiles
        store=manager.profile_dir(a['id'])/'store/jetski-standalone-oauth-token';saved=store.read_bytes()
        state.session_ops.begin(source='native')
        atomic_json(home/'.gemini/jetski-standalone-oauth-token',dict(token=dict(refresh_token='new-A'),id_token_email='a@example.invalid'))
        with pytest.raises(ValueError):state.request_exit()
        with pytest.raises(ValueError):state.request_start(a['id'])
        await state.stop_and_save()
        assert store.read_bytes()==saved and len(manager.list_profiles())==1
    asyncio.run(scenario())


def test_stale_profile_card_cannot_exit_the_current_other_profile(tmp_path):
    async def scenario():
        state,a,home=setup(tmp_path)
        with pytest.raises(ValueError,match='当前选中的配置已变化'):state.request_exit('b'*32)
        assert state.exit_task is None and state.active==a['id']
        with pytest.raises(ValueError,match='当前配置已变化'):await state.session_ops.exit('b'*32)
        assert state.active==a['id'] and state.supervisor.process is not None
    asyncio.run(scenario())


def test_start_after_stopping_login_environment_keeps_waiting_and_does_not_restore_an_account(tmp_path):
    async def scenario():
        state,a,home=setup(tmp_path);calls=[]
        state.active=None;state.cfg['native_logged_out']=True
        spec=state.profiles.native_runtime.selected;state.sessions.exited(spec)
        async def empty(runtime):
            calls.append(runtime['id']);state.login_environment_open=True
        state.session_ops.start_empty=empty
        state.request_open_login();await state.launch_task
        assert calls==[spec['id']] and state.active is None and state.sessions.state['waiting']
        assert len(state.profiles.list_profiles())==1 and not state.opening_login and not state.busy
    asyncio.run(scenario())


def test_manual_stop_is_persisted_and_prevents_login_environment_autoboot(tmp_path):
    async def scenario():
        cfg=config(tmp_path);cfg.update(engine_stopped=True,login_environment=True,native_logged_out=True)
        app=create_app(cfg,supervisor=FakeSupervisor());s=app['state'];s.sessions.exited()
        calls=[];s.request_open_login=lambda:calls.append('opened')
        runner,port=await start(app)
        try:
            await asyncio.sleep(.05)
            assert calls==[] and s.supervisor.process is None
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                await login(client,'http://127.0.0.1:'+str(port))
                response=await client.post('http://127.0.0.1:'+str(port)+'/__suite/api/system/stop')
                assert response.status==200 and s.cfg['engine_stopped'] and not s.login_environment_open
                assert json.loads((tmp_path/'config.json').read_text())['engine_stopped']
        finally:await runner.cleanup()
    asyncio.run(scenario())


def test_open_login_repairs_detached_login_generation_with_no_active_card(tmp_path,monkeypatch):
    async def scenario():
        state,a,home=setup(tmp_path);state.active=None;state.cfg['native_logged_out']=True
        state.sessions.restored(a['id'],state.profiles.installed_token(home/'.gemini/jetski-standalone-oauth-token'))
        calls=[]
        async def local_exit():
            calls.append('exit');state.sessions.exited(state.session_ops.runtime());state.login_environment_open=True
        monkeypatch.setattr(state.session_ops,'exit',local_exit)
        state.request_open_login();await state.launch_task
        assert calls==['exit'] and state.sessions.state['waiting'] and state.active is None
        assert state.profiles.get_profile(a['id']) is not None and not state.opening_login
    asyncio.run(scenario())


def test_detached_session_uses_its_working_environment_before_other_discovered_homes(tmp_path):
    async def scenario():
        state,a,home=setup(tmp_path);selected=state.profiles.native_runtime.selected
        working=dict(selected,id='b'*32,native_home=str(tmp_path/'other-working'),native_config_dir=str(tmp_path/'other-working/.config/Antigravity'))
        state.profiles.native_runtime.detach();state.sessions.exited(working)
        assert state.session_ops.runtime()['id']==working['id']
    asyncio.run(scenario())


@pytest.mark.skipif(os.name=='nt',reason='Full runtime exit uses Linux shared-history symlinks')
def test_exit_from_stopped_profile_preserves_its_proxy_for_login_environment(tmp_path,monkeypatch):
    async def scenario():
        state,a,home=setup(tmp_path);state.supervisor.stop_antigravity();state.native_runtime_proxy_id=None
        captures=[]
        async def start_empty(spec):
            captures.append(state.cfg['login_proxy_id']);state.login_environment_open=True
        async def probe(*args,**kwargs):return dict(token_present=False)
        monkeypatch.setattr(state.session_ops,'start_empty',start_empty)
        monkeypatch.setattr('auth_probe.auth_status',probe)
        await state.session_ops.exit()
        assert captures==['node-A'] and state.native_runtime_proxy_id=='node-A'
    asyncio.run(scenario())


@pytest.mark.parametrize('probe', [False,None])
@pytest.mark.skipif(os.name=='nt',reason='Full runtime exit uses Linux shared-history symlinks')
def test_exit_preserves_saved_session_and_chats_and_rolls_back_unconfirmed_clear(tmp_path,monkeypatch,probe):
    async def scenario():
        state,a,home=setup(tmp_path);a['proxy_id']='';state.native_runtime_proxy_id=None
        manager=state.profiles;chat=home/'.gemini/antigravity/conversations/chat.pb';chat.parent.mkdir(parents=True);chat.write_bytes(b'keep chats')
        token=home/'.gemini/jetski-standalone-oauth-token';old=token.read_bytes()
        async def mock_probe(*args,**kwargs):return dict(token_present=probe)
        monkeypatch.setattr('auth_probe.auth_status',mock_probe)
        if probe is None:
            with pytest.raises(ValueError):await state.session_ops.exit()
            assert token.read_bytes()==old and state.active==a['id'] and state.loaded==a['id']
        else:
            await state.session_ops.exit()
            assert not token.exists() and state.active is None and state.sessions.state['waiting']
            assert state.login_environment_open and state.exit_state['phase']=='ready'
        assert (manager.profile_dir(a['id'])/'store/jetski-standalone-oauth-token').read_bytes()==old
        assert chat.read_bytes()==b'keep chats' and len(manager.list_profiles())==1
        if state.outbound_bridge:await state.outbound_bridge.close()
    asyncio.run(scenario())


def test_guard_preserves_asar_and_inline_sandbox_preload_updates_idempotently(tmp_path,monkeypatch):
    archive=tmp_path/'client/resources/app.asar';archive.parent.mkdir(parents=True)
    data={'dist/main.js':b'"use strict";require("electron");',
          'dist/preload.js':b'"use strict";require("electron").contextBridge.exposeInMainWorld("old",{});',
          'dist/utils.js':b'function createWindow(url){const win={};void win.loadURL(url);return win;}',
          'other.txt':b'unchanged'}
    header={'files':{'unpacked.bin':{'unpacked':True,'size':5}}}
    original=encode_archive(header,data);archive.write_bytes(original);archive.chmod(0o644)
    binary=tmp_path/'client/antigravity';binary.write_bytes(b'fixture')
    monkeypatch.setattr('discovery.engine_processes',lambda:[])
    assert install(binary,tmp_path/'data')['installed']
    h,f=read_archive(archive)
    assert h['files']['unpacked.bin']['unpacked'] and f['other.txt']==data['other.txt']
    assert f['dist/preload.js'].startswith(b'"use strict";')
    assert b"require('./suite-native-preload.js')" not in f['dist/preload.js']
    assert b'antigravitySuiteNative' in f['dist/preload.js']
    first=archive.read_bytes();assert install(binary,tmp_path/'data',True)['installed']
    assert archive.read_bytes()==first
    from desktop_guard import payloads as real_payloads
    altered=real_payloads();altered['dist/suite-native-main.js']+=b'\n/* update */'
    monkeypatch.setattr('desktop_guard.payloads',lambda:altered)
    with pytest.raises(ValueError):install(binary,tmp_path/'data',True)
    assert install(binary,tmp_path/'data')['installed']
    assert restore(tmp_path/'data') and archive.read_bytes()==original
    if os.name!='nt':assert archive.stat().st_mode&0o777==0o644


@pytest.mark.parametrize('fixture',[FIXTURE,FIXTURE.replace('var e=g.authResult;if(e?.hasValidAuth)this.result=e;','var d=g.authResult;if(d?.hasValidAuth)this.result=d;')])
def test_client_transform_parity_and_logout_never_changes_auth_or_calls_rpc(tmp_path,fixture):
    source,supported=transform_client(fixture)
    assert supported
    assert 'finally' not in source[source.index('async logout(){'):source.index('};',source.index('async logout(){'))]
    script=Path(__file__).resolve().parents[1]/'static/client-transform.js'
    node='const t=require('+json.dumps(str(script))+');process.stdout.write(JSON.stringify(t.transform('+json.dumps(fixture)+')));'
    native=json.loads(subprocess.check_output(['node','-e',node],text=True))
    assert native==dict(source=source,supported=True)
    execution='''let warnings=0,rpcs=0,begins=0,completions=0;
globalThis.__AGY_SESSION__={warn:async()=>{warnings++},begin:async()=>{begins++;return 'nonce'},complete:async(n,v)=>{if(n!=='nonce'||!v)throw Error('bad');completions++}};
'''+source+'''
(async()=>{const a=new AuthFixture();a.isLoggingOut=false;a.result='kept';a._lsClient={authLogout:async()=>{rpcs++},login:async()=>({authResult:{hasValidAuth:true}})};
await a.logout();if(warnings!==1||rpcs!==0||a.isLoggingOut||a.result!=='kept')throw Error('logout leaked');
await a.loginWithRedirect();if(begins!==1||completions!==1)throw Error('login untracked');})();'''
    subprocess.run(['node','-e',execution],check=True)
    failed='''let canceled=0;globalThis.__AGY_SESSION__={begin:async()=> 'nonce',complete:async(n,v)=>{if(!v)canceled++}};'''+source+'''
(async()=>{const a=new AuthFixture();a._lsClient={login:async()=>{throw Error('mock failure')}};try{await a.loginWithRedirect()}catch{}if(canceled!==1)throw Error('failed login left pending');})();'''
    subprocess.run(['node','-e',failed],check=True)
    assert not transform_client('unsupported')[1]
    assert transform_client(source)[0]==source


def test_gateway_blocks_auth_logout_before_any_upstream_request(tmp_path):
    async def scenario():
        received=[]
        async def backend(request):
            received.append(request.path)
            return web.Response(text='<html><head data-test="yes"><script src="/main.js?old=1"></script></head><body>Fixture</body></html>',content_type='text/html')
        upstream=web.Application();upstream.router.add_route('*','/{tail:.*}',backend);br,bp=await start(upstream)
        app=create_app(config(tmp_path),supervisor=FakeSupervisor(bp));sr,sp=await start(app);url='http://127.0.0.1:'+str(sp)
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as c:
                await login(c,url)
                for path in ['/exa.language_server_pb.LanguageServerService/AuthLogout','/exa.language_server_pb.LanguageServerService/%41uthLogout?x=1']:
                    response=await c.post(url+path,json={})
                    assert response.status==403 and (await response.json())['blocked_logout']
                assert received==[]
                page=await(await c.get(url+'/')).text()
                assert '<head data-test="yes"><script src="/__suite/logout-guard.js">' in page
                assert '/main.js?agy_suite=' in page and '?old=1' not in page
                # Unrelated sign-outs remain usable.
                assert (await c.post(url+'/plugin/logout')).status==200
        finally:await sr.cleanup();await br.cleanup()
    asyncio.run(scenario())


def test_native_capability_rejects_other_hosts_unknown_operations_and_secret_export(tmp_path):
    async def scenario():
        cfg=config(tmp_path);app=create_app(cfg,supervisor=FakeSupervisor());s=app['state'];spec=s.session_ops.runtime(create=True)
        spec=s.native_controls.publish(spec);cap=json.loads((Path(spec['native_home'])/'.cache/antigravity-suite/native-control.json').read_text())
        s.sessions.state['working']=spec
        runner,port=await start(app);url='http://127.0.0.1:'+str(port)+'/__suite/native/control'
        try:
            async with aiohttp.ClientSession() as c:
                body=dict(runtime_id=spec['id'],operation='status')
                assert (await c.post(url,json=body)).status==403
                headers={'X-Antigravity-Native-Control':cap['token']}
                response=await c.post(url,json=body,headers=headers)
                assert response.status==200
                text=await response.text()
                assert cfg['session_secret'] not in text and 'auth_password_hash' not in text
                body['operation']='system/config';assert (await c.post(url,json=body,headers=headers)).status==400
                body['operation']='status';body['runtime_id']='0'*32
                assert (await c.post(url,json=body,headers=headers)).status==403
        finally:await runner.cleanup()
    asyncio.run(scenario())
