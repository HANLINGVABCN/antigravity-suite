import asyncio
import base64
import copy
import json
import time

import aiohttp
import pytest

from server import create_app,valid_session
from test_suite import config,FakeSupervisor,start,login,PASSWORD
from two_factor import totp,matching_counter,new_secret,new_recovery_codes,check_factor,recovery_hash


def test_totp_rfc6238_sha1_vectors_and_window():
    secret=base64.b32encode(b'12345678901234567890').decode()
    for seconds,expected in [(59,'94287082'),(1111111109,'07081804'),(1111111111,'14050471'),(1234567890,'89005924'),(2000000000,'69279037'),(20000000000,'65353130')]:
        assert totp(secret,seconds//30,digits=8)==expected
    code=totp(secret,100)
    assert matching_counter(secret,code,now=3000)==100
    assert matching_counter(secret,code,now=3030)==100
    assert matching_counter(secret,code,now=2970)==100
    assert matching_counter(secret,code,now=3060) is None
    assert matching_counter(secret,code,now=3000,last_counter=100) is None
    for bad in [None,'','not-code','１２３４５６','123456789']:
        assert matching_counter(secret,bad,now=3000) is None


def test_recovery_codes_are_hashed_and_single_use():
    codes,hashes=new_recovery_codes()
    assert len(set(codes))==len(hashes)==10
    cfg=dict(mfa_secret=new_secret(),mfa_last_counter=-1,mfa_recovery_hashes=hashes)
    assert codes[0] not in str(hashes)
    updates=check_factor(cfg,codes[0].upper())
    assert len(updates['mfa_recovery_hashes'])==9
    cfg.update(updates)
    assert check_factor(cfg,codes[0]) is None
    assert recovery_hash('invalid') is None


async def setup(client,url):
    response=await client.post(url+'/__suite/api/system/security/setup',json={'current_password':PASSWORD})
    assert response.status==200
    data=await response.json()
    code=totp(data['secret'],int(time.time()//30))
    return data,code


def test_optional_setup_enable_login_replay_recovery_and_disable(tmp_path):
    async def scenario():
        app=create_app(config(tmp_path),supervisor=FakeSupervisor());state=app['state']
        runner,port=await start(app);url='http://127.0.0.1:%d'%port
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as admin,aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as other:
                assert (await login(admin,url)).status==200
                assert (await login(other,url)).status==200
                old_token=next(cookie.value for cookie in other.cookie_jar if cookie.key=='ag_session')
                status=await (await admin.get(url+'/__suite/api/system/security/status')).json()
                assert status['enabled'] is False
                data,code=await setup(admin,url)
                assert data['otpauth_uri'].startswith('otpauth://totp/') and data['secret'] in data['otpauth_uri']
                assert not state.cfg.get('mfa_enabled',False)
                assert 'mfa_secret' not in await (await admin.get(url+'/__suite/api/status')).text()
                qr=await admin.get(url+'/__suite/api/system/security/qr',params={'setup_token':data['setup_token']})
                assert qr.status==200 and qr.headers['Content-Type'].startswith('image/svg+xml')
                assert qr.headers['Cache-Control']=='no-store' and b'<svg' in await qr.read()
                response=await admin.post(url+'/__suite/api/system/security/enable',json={'setup_token':data['setup_token'],'current_password':PASSWORD,'code':code})
                assert response.status==200
                enabled=await response.json();codes=enabled['recovery_codes']
                assert enabled['enabled'] and len(codes)==10 and state.cfg['mfa_enabled']
                assert not valid_session(old_token,state.cfg)
                assert (await other.get(url+'/__suite/api/status')).status==401
                assert (await admin.get(url+'/__suite/api/status')).status==200
                persisted=json.loads((tmp_path/'config.json').read_text())
                assert persisted['mfa_enabled'] and persisted['mfa_secret']==data['secret']
                assert all(value not in str(persisted) for value in codes)
                missing=await login(other,url)
                assert missing.status==401 and (await missing.json())['mfa_required']
                assert not missing.cookies
                replay=await other.post(url+'/__suite/api/login',json={'username':'admin','password':PASSWORD,'code':code})
                assert replay.status==401
                counter=state.cfg['mfa_last_counter']+1
                fresh=totp(data['secret'],counter)
                response=await other.post(url+'/__suite/api/login',json={'username':'admin','password':PASSWORD,'code':fresh})
                assert response.status==200
                response=await other.post(url+'/__suite/api/login',json={'username':'admin','password':PASSWORD,'code':fresh})
                assert response.status==401
                response=await other.post(url+'/__suite/api/login',json={'username':'admin','password':PASSWORD,'code':codes[0]})
                assert response.status==200
                response=await other.post(url+'/__suite/api/login',json={'username':'admin','password':PASSWORD,'code':codes[0]})
                assert response.status==401
                status=await (await admin.get(url+'/__suite/api/system/security/status')).json()
                assert status['recovery_codes_remaining']==9 and 'secret' not in status
                response=await admin.post(url+'/__suite/api/system/security/disable',json={'current_password':PASSWORD,'code':codes[1]})
                assert response.status==200 and not (await response.json())['enabled']
                assert not state.cfg['mfa_secret'] and state.cfg['mfa_recovery_hashes']==[]
                assert (await login(other,url)).status==200
        finally:await runner.cleanup()
    asyncio.run(scenario())


def test_setup_requires_password_and_matching_session_and_expires(tmp_path):
    async def scenario():
        app=create_app(config(tmp_path),supervisor=FakeSupervisor());state=app['state']
        runner,port=await start(app);url='http://127.0.0.1:%d'%port
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as admin:
                assert (await admin.post(url+'/__suite/api/system/security/setup',json={'current_password':PASSWORD})).status==401
                await login(admin,url)
                wrong=await admin.post(url+'/__suite/api/system/security/setup',json={'current_password':'wrong'})
                assert wrong.status==400 and not state.pending_mfa
                data,code=await setup(admin,url)
                item=state.pending_mfa[data['setup_token']];item['session']='wrong-session'
                response=await admin.post(url+'/__suite/api/system/security/enable',json={'current_password':PASSWORD,'setup_token':data['setup_token'],'code':code})
                assert response.status==400 and not state.cfg.get('mfa_enabled')
                assert (await admin.get(url+'/__suite/api/system/security/qr',params={'setup_token':data['setup_token']})).status==400
                item['expires_at']=0
                response=await admin.post(url+'/__suite/api/system/security/enable',json={'current_password':PASSWORD,'setup_token':data['setup_token'],'code':code})
                assert response.status==400 and not state.pending_mfa
                data,_=await setup(admin,url)
                await admin.post(url+'/__suite/api/system/security/cancel',json={'setup_token':data['setup_token']})
                assert not state.pending_mfa and not state.cfg.get('mfa_enabled')
        finally:await runner.cleanup()
    asyncio.run(scenario())


def test_wrong_second_factor_cannot_disable_or_change_password(tmp_path):
    async def scenario():
        cfg=config(tmp_path);codes,hashes=new_recovery_codes()
        cfg.update(mfa_enabled=True,mfa_secret=new_secret(),mfa_last_counter=-1,mfa_recovery_hashes=hashes)
        app=create_app(cfg,supervisor=FakeSupervisor());state=app['state']
        runner,port=await start(app);url='http://127.0.0.1:%d'%port
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as admin:
                response=await admin.post(url+'/__suite/api/login',json={'username':'admin','password':PASSWORD,'code':codes[0]})
                assert response.status==200
                original=copy.deepcopy(state.cfg)
                response=await admin.post(url+'/__suite/api/system/security/disable',json={'current_password':PASSWORD,'code':'invalid'})
                assert response.status==400 and state.cfg==original
                response=await admin.post(url+'/__suite/api/system/config',json={'auth_pass':'new-password-long-enough','current_password':PASSWORD,'code':'invalid'})
                assert response.status==400 and state.cfg==original
                response=await admin.post(url+'/__suite/api/system/security/recovery',json={'current_password':PASSWORD,'code':codes[1]})
                assert response.status==200
                new_codes=(await response.json())['recovery_codes']
                assert len(new_codes)==10 and check_factor(state.cfg,codes[2]) is None
                response=await admin.post(url+'/__suite/api/system/config',json={'auth_pass':'new-password-long-enough','current_password':PASSWORD,'code':new_codes[0]})
                assert response.status==200
                assert (await admin.get(url+'/__suite/api/status')).status==401
                response=await admin.post(url+'/__suite/api/login',json={'username':'admin','password':'new-password-long-enough','code':new_codes[1]})
                assert response.status==200
        finally:await runner.cleanup()
    asyncio.run(scenario())


def test_second_factor_persistence_failure_grants_no_session(tmp_path,monkeypatch):
    async def scenario():
        import security_api
        cfg=config(tmp_path);codes,hashes=new_recovery_codes()
        cfg.update(mfa_enabled=True,mfa_secret=new_secret(),mfa_recovery_hashes=hashes,mfa_last_counter=-1)
        app=create_app(cfg,supervisor=FakeSupervisor());state=app['state'];original=copy.deepcopy(cfg)
        runner,port=await start(app);url='http://127.0.0.1:%d'%port
        try:
            async with aiohttp.ClientSession() as client:
                def fail(*args):raise OSError('simulated persistence failure')
                with monkeypatch.context() as patch:
                    patch.setattr(security_api,'atomic_json',fail)
                    response=await client.post(url+'/__suite/api/login',json={'username':'admin','password':PASSWORD,'code':codes[0]})
                    assert response.status==500 and not response.cookies and state.cfg==original
        finally:await runner.cleanup()
    asyncio.run(scenario())


def test_recovery_code_cannot_be_used_by_two_concurrent_logins(tmp_path):
    async def scenario():
        cfg=config(tmp_path);codes,hashes=new_recovery_codes()
        cfg.update(mfa_enabled=True,mfa_secret=new_secret(),mfa_recovery_hashes=hashes,mfa_last_counter=-1)
        runner,port=await start(create_app(cfg,supervisor=FakeSupervisor()));url='http://127.0.0.1:%d'%port
        try:
            async with aiohttp.ClientSession() as client:
                async def attempt():
                    response=await client.post(url+'/__suite/api/login',json={'username':'admin','password':PASSWORD,'code':codes[0]})
                    return response.status
                assert sorted(await asyncio.gather(attempt(),attempt()))==[200,401]
        finally:await runner.cleanup()
    asyncio.run(scenario())


def test_enabling_authenticator_revokes_existing_ide_websocket(tmp_path):
    from aiohttp import web
    async def scenario():
        async def echo(request):
            socket=web.WebSocketResponse();await socket.prepare(request)
            async for message in socket:
                if message.type==aiohttp.WSMsgType.TEXT:await socket.send_str(message.data)
            return socket
        backend=web.Application();backend.router.add_get('/ws',echo)
        backend_runner,backend_port=await start(backend)
        app=create_app(config(tmp_path),supervisor=FakeSupervisor(backend_port))
        runner,port=await start(app);url='http://127.0.0.1:%d'%port
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as admin:
                await login(admin,url)
                socket=await admin.ws_connect(url+'/ws')
                await socket.send_str('test');assert (await socket.receive()).data=='test'
                data,code=await setup(admin,url)
                response=await admin.post(url+'/__suite/api/system/security/enable',json={'setup_token':data['setup_token'],'current_password':PASSWORD,'code':code})
                assert response.status==200
                message=await asyncio.wait_for(socket.receive(),2)
                assert message.type in (aiohttp.WSMsgType.CLOSED,aiohttp.WSMsgType.CLOSE,aiohttp.WSMsgType.ERROR)
                assert (await admin.get(url+'/__suite/api/status')).status==200
                await socket.close()
        finally:await runner.cleanup();await backend_runner.cleanup()
    asyncio.run(scenario())
