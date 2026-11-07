import asyncio
import io
import json
import os
import secrets
import ssl
import tarfile
import time
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

from config import atomic_json, hash_password
from profile_manager import ProfileManager
from server import create_app, session_token, valid_session

PASSWORD = 'a-long-test-password '

class FakeProcess:
    def poll(self): return None

class FakeSupervisor:
    def __init__(self, port=None):
        self.active_port=port; self.process=None; self.is_restarting=False
        self.events=[]; self.current_proxy_url=None; self.fail=False
    def installed(self): return True
    def stop_antigravity(self):
        self.events.append('stop'); self.process=None; self.active_port=None
    def start_antigravity(self,proxy=None):
        self.events.append('start')
        if self.fail:
            self.fail=False
            raise RuntimeError('intentional startup failure')
        self.process=FakeProcess(); self.active_port=45000; self.current_proxy_url=proxy
        return self.active_port
    def close(self): self.stop_antigravity()

def config(tmp):
    return dict(data_dir=str(tmp), profiles_dir=str(tmp/'profiles'), runtime_home=str(tmp/'home'),
                antigravity_bin='/missing-test-binary', display_num=':98',
                auth_user='admin', auth_password_hash=hash_password(PASSWORD),session_secret=secrets.token_hex(32),
                active_profile_id=None, default_profile_id=None,listen_host='127.0.0.1',listen_port=18888,
                cookie_secure='auto',trusted_proxy=False,backend_scheme='http',backend_verify_tls=False,
                oauth_client_id='example.apps.googleusercontent.com',
                core_deb_url='https://storage.googleapis.com/test.deb')

async def start(app,ctx=None):
    runner=web.AppRunner(app); await runner.setup()
    site=web.TCPSite(runner,'127.0.0.1',0,ssl_context=ctx); await site.start()
    port=site._server.sockets[0].getsockname()[1]
    return runner,port

async def login(client,url,password=PASSWORD,headers=None):
    response=await client.post(url+'/__suite/api/login',json={'username':'admin','password':password},headers=headers)
    return response

def test_http_login_logout_and_csrf(tmp_path):
    async def scenario():
        app=create_app(config(tmp_path),supervisor=FakeSupervisor())
        runner,port=await start(app); url='http://127.0.0.1:'+str(port)
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as c:
                assert (await c.get(url+'/__suite/api/status')).status==401
                assert (await login(c,url,PASSWORD.strip())).status==401
                response=await login(c,url)
                assert response.status==200
                assert not response.cookies['ag_session']['secure']
                assert (await c.get(url+'/__suite/api/status')).status==200
                assert (await c.post(url+'/__suite/api/profiles',json={'name':'bad'},headers={'Origin':'https://evil.example'})).status==403
                assert (await c.post(url+'/__suite/api/logout')).status==200
                assert (await c.get(url+'/__suite/api/status')).status==401
        finally: await runner.cleanup()
    asyncio.run(scenario())

def test_secure_cookie_behind_trusted_proxy(tmp_path):
    async def scenario():
        cfg=config(tmp_path); cfg['trusted_proxy']=True
        runner,port=await start(create_app(cfg,supervisor=FakeSupervisor()))
        try:
            async with aiohttp.ClientSession() as c:
                response=await login(c,'http://127.0.0.1:'+str(port),headers={'X-Forwarded-Proto':'https'})
                assert response.cookies['ag_session']['secure']
        finally: await runner.cleanup()
    asyncio.run(scenario())

def test_malformed_expired_future_and_rotated_session(tmp_path):
    cfg=config(tmp_path); token=session_token(cfg)
    assert valid_session(token,cfg)
    for value in [None,'bad','admin.bad.x','admin.99999999999.x','admin.0.x']:
        assert not valid_session(value,cfg)
    cfg['session_secret']=secrets.token_hex(32)
    assert not valid_session(token,cfg)

def test_streaming_grpc_post_and_assets_are_not_buffered(tmp_path):
    async def scenario():
        finish=asyncio.Event(); received={}
        async def stream(req):
            received['body']=await req.read(); received['headers']=req.headers
            response=web.StreamResponse(headers={'Content-Type':'application/grpc-web+proto','X-Grpc-Web':'1'})
            await response.prepare(req)
            await response.write(b'first-frame')
            await finish.wait()
            await response.write(b'\x80\x00\x00\x00\x10grpc-status: 0\r\n')
            await response.write_eof(); return response
        backend=web.Application(); backend.router.add_post('/exa.test/Subscribe',stream)
        br,bp=await start(backend)
        supervisor=FakeSupervisor(bp)
        app=create_app(config(tmp_path),supervisor=supervisor)
        sr,sp=await start(app); url='http://127.0.0.1:'+str(sp)
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as c:
                await login(c,url)
                async def chunks():
                    yield b'request-'; await asyncio.sleep(.01); yield b'body'
                response=await c.post(url+'/exa.test/Subscribe?x=a%2Fb',data=chunks(),headers={'Connection':'close'})
                assert await asyncio.wait_for(response.content.readexactly(11),2)==b'first-frame'
                assert not finish.is_set()
                assert received['body']==b'request-body'
                assert received['headers'].get('Connection','').lower()!='close'
                assert 'ag_session' not in received['headers'].get('Cookie','')
                finish.set()
                assert b'grpc-status: 0' in await response.read()
        finally:
            finish.set(); await sr.cleanup(); await br.cleanup()
    asyncio.run(scenario())

def test_websocket_binary_and_subprotocol(tmp_path):
    async def scenario():
        async def echo(req):
            ws=web.WebSocketResponse(protocols=['grpc-websockets']); await ws.prepare(req)
            async for message in ws:
                if message.type==aiohttp.WSMsgType.BINARY: await ws.send_bytes(message.data)
                elif message.type==aiohttp.WSMsgType.TEXT: await ws.send_str(message.data)
            return ws
        backend=web.Application(); backend.router.add_get('/ws',echo)
        br,bp=await start(backend); sr,sp=await start(create_app(config(tmp_path),supervisor=FakeSupervisor(bp)))
        url='http://127.0.0.1:'+str(sp)
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as c:
                await login(c,url)
                ws=await c.ws_connect(url+'/ws',protocols=['grpc-websockets'])
                assert ws.protocol=='grpc-websockets'
                await ws.send_bytes(b'\x00\x01\x02')
                assert (await asyncio.wait_for(ws.receive(),2)).data==b'\x00\x01\x02'
                await ws.send_str('hello')
                assert (await asyncio.wait_for(ws.receive(),2)).data=='hello'
                await ws.close()
        finally: await sr.cleanup(); await br.cleanup()
    asyncio.run(scenario())

def test_https_backend_and_floating_injection(tmp_path):
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
    now=datetime.datetime.now(datetime.timezone.utc)
    cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(days=1)).not_valid_after(now+datetime.timedelta(days=1)).sign(key,hashes.SHA256())
    certfile=tmp_path/'cert.pem'; keyfile=tmp_path/'key.pem'
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(certfile,keyfile)
    async def scenario():
        async def html(req):
            assert req.headers['Origin'].startswith('https://127.0.0.1:')
            return web.Response(text='<html><body>IDE</body></html>',content_type='text/html')
        backend=web.Application(); backend.router.add_get('/',html); backend.router.add_get('/onboarding',html)
        br,bp=await start(backend,ctx); cfg=config(tmp_path); cfg['backend_scheme']='https'
        sr,sp=await start(create_app(cfg,supervisor=FakeSupervisor(bp))); url='http://127.0.0.1:'+str(sp)
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as c:
                await login(c,url)
                response=await c.get(url+'/',headers={'Origin':url})
                assert response.status==200
                text=await response.text()
                assert text.count('/__suite/floating.js')==1
                assert len(text.encode())==int(response.headers['Content-Length'])
                assert (await c.get(url+'/__suite/floating.js')).status==200
                response=await c.get(url+'/onboarding?login=true',headers={'Origin':url,'Accept':'text/html'})
                assert (await response.text()).count('/__suite/floating.js')==1
        finally: await sr.cleanup(); await br.cleanup()
    asyncio.run(scenario())

@pytest.mark.skipif(os.name=='nt',reason='Linux deployment uses filesystem symlinks; run on target server')
def test_profile_switch_clears_missing_token_and_stale_files(tmp_path):
    manager=ProfileManager(tmp_path,tmp_path/'profiles')
    a=manager.create_profile('A'); b=manager.create_profile('B')
    a_store=manager.profile_dir(a['id'])/'store'
    (a_store/'Antigravity_config').mkdir()
    (a_store/'Antigravity_config'/'old.json').write_text('{}')
    (a_store/'jetski-standalone-oauth-token').write_text('secret-A')
    manager.restore_profile(a['id'])
    manager.restore_profile(b['id'])
    assert not manager.runtime_targets[0][0].exists()
    assert not manager.runtime_targets[1][0].exists()
    manager.restore_profile(a['id'])
    manager.runtime_targets[0][0].unlink()
    manager.save_active_to_profile(a['id'])
    assert not (a_store/'jetski-standalone-oauth-token').exists()

@pytest.mark.skipif(os.name=='nt',reason='Linux deployment uses filesystem symlinks; run on target server')
def test_failed_switch_restores_previous_account(tmp_path):
    async def scenario():
        cfg=config(tmp_path); supervisor=FakeSupervisor()
        app=create_app(cfg,supervisor=supervisor); s=app['state']
        a=s.profiles.create_profile('A',email='a@example.invalid'); b=s.profiles.create_profile('B')
        await s.activate(a['id'])
        runtime=s.profiles.runtime_targets[0][0]
        runtime.parent.mkdir(parents=True,exist_ok=True)
        atomic_json(runtime,dict(token=dict(refresh_token='account-A'),id_token_email='a@example.invalid'))
        supervisor.fail=True
        with pytest.raises(RuntimeError): await s.activate(b['id'])
        assert s.active==a['id']
        assert json.loads(runtime.read_text())['token']['refresh_token']=='account-A'
        assert supervisor.active_port and not s.busy
        assert supervisor.events.index('stop')<supervisor.events.index('start')
    asyncio.run(scenario())

def test_import_export_and_archive_traversal(tmp_path):
    m=ProfileManager(tmp_path,tmp_path/'profiles'); p=m.create_profile('Account')
    token=m.profile_dir(p['id'])/'store/jetski-standalone-oauth-token'; token.write_text('saved')
    archive=tmp_path/'export.tar.gz'; m.export_profile(p['id'],archive)
    clone=m.import_profile(archive)
    assert clone['id']!=p['id']
    assert (m.profile_dir(clone['id'])/'store/jetski-standalone-oauth-token').read_text()=='saved'
    for name,kind in [('../outside',tarfile.REGTYPE),('/absolute',tarfile.REGTYPE),('profile/store/link',tarfile.SYMTYPE)]:
        bad=tmp_path/'bad.tar.gz'
        with tarfile.open(bad,'w:gz') as tar:
            info=tarfile.TarInfo(name); info.type=kind; info.linkname='/outside'
            tar.addfile(info,io.BytesIO())
        with pytest.raises(ValueError): m.import_profile(bad)
    with pytest.raises(ValueError): m.resolve_file(p['id'],'../../outside')

def test_import_api_and_file_backup(tmp_path):
    async def scenario():
        app=create_app(config(tmp_path),supervisor=FakeSupervisor()); s=app['state']
        p=s.profiles.create_profile('Account'); file=s.profiles.profile_dir(p['id'])/'store/Antigravity_config/settings.json'
        file.parent.mkdir(parents=True); file.write_text('{"before":1}')
        archive=tmp_path/'profile.tar.gz'; s.profiles.export_profile(p['id'],archive)
        runner,port=await start(app); url='http://127.0.0.1:'+str(port)
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as c:
                await login(c,url)
                form=aiohttp.FormData(); form.add_field('file',archive.read_bytes(),filename='profile.tar.gz',content_type='application/gzip')
                response=await c.post(url+'/__suite/api/profiles/import',data=form)
                assert response.status==200 and (await response.json())['ok']
                response=await c.post(url+'/__suite/api/profiles/file',json={'id':p['id'],'path':'Antigravity_config/settings.json','content':'{"after":2}'})
                assert response.status==200
                assert json.loads(file.read_text())=={'after':2}
                assert len(list((tmp_path/'file-backups').rglob('settings.json')))==1
                response=await c.post(url+'/__suite/api/profiles/file',json={'id':p['id'],'path':'Antigravity_config/settings.json','content':'bad-json'})
                assert response.status==400
        finally: await runner.cleanup()
    asyncio.run(scenario())

def test_oauth_state_does_not_fall_back_to_another_session(tmp_path):
    async def scenario():
        app=create_app(config(tmp_path),supervisor=FakeSupervisor()); runner,port=await start(app)
        url='http://127.0.0.1:'+str(port)
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as c:
                await login(c,url)
                data=await (await c.post(url+'/__suite/api/auth/google/start')).json()
                response=await c.post(url+'/__suite/api/auth/google/complete',json={'code':'fake','state':'wrong','name':'X'})
                assert response.status==400
                assert data['state'] in app['state'].pending_oauth
        finally: await runner.cleanup()
    asyncio.run(scenario())

def test_installer_failure_is_reported_and_task_is_deduplicated(tmp_path):
    async def scenario():
        from core_installer import CoreInstaller
        installer=CoreInstaller(config(tmp_path),FakeSupervisor())
        async def fail():
            await asyncio.sleep(.01)
            installer.state.update(status='error',message='test download failure')
        installer.install=fail
        installer.start(); task=installer.task; installer.start()
        assert installer.task is task
        await task
        assert installer.state['status']=='error'
    asyncio.run(scenario())

def test_shutdown_does_not_overwrite_unloaded_profile(tmp_path):
    async def scenario():
        cfg=config(tmp_path)
        manager=ProfileManager(tmp_path,tmp_path/'profiles')
        p=manager.create_profile('Saved account')
        token=manager.profile_dir(p['id'])/'store/jetski-standalone-oauth-token'
        token.write_text('must-survive')
        cfg['active_profile_id']=p['id']
        app=create_app(cfg,profile_mgr=manager,supervisor=FakeSupervisor())
        runner,_=await start(app)
        await runner.cleanup()
        assert token.read_text()=='must-survive'
    asyncio.run(scenario())

def test_raw_forwarder_preserves_half_close(tmp_path):
    import importlib.util
    spec=importlib.util.spec_from_file_location('native_forwarder',Path(__file__).parents[1]/'tools/antigravity-forwarder.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    async def scenario():
        async def backend(reader,writer):
            data=await reader.read()
            writer.write(b'response:'+data); await writer.drain(); writer.close(); await writer.wait_closed()
        target=await asyncio.start_server(backend,'127.0.0.1',0)
        module.current_target_port=target.sockets[0].getsockname()[1]
        forwarder=await asyncio.start_server(module.handle_client,'127.0.0.1',0)
        try:
            reader,writer=await asyncio.open_connection('127.0.0.1',forwarder.sockets[0].getsockname()[1])
            writer.write(b'request'); await writer.drain(); writer.write_eof()
            assert await asyncio.wait_for(reader.read(),2)==b'response:request'
            writer.close(); await writer.wait_closed()
        finally:
            forwarder.close(); target.close(); await forwarder.wait_closed(); await target.wait_closed()
    asyncio.run(scenario())

def test_token_presence_does_not_claim_google_authorization(tmp_path):
    async def scenario():
        async def html(req):
            return web.Response(text='<script>window.__APP_CONFIG__={"csrfToken":"native-csrf"}</script>',content_type='text/html')
        async def rpc(req):
            assert req.headers['x-codeium-csrf-token']=='native-csrf'
            if req.match_info['method']=='HasAuthToken': return web.json_response({'hasToken':True})
            return web.json_response({'authResult':{'uiMessage':'Not currently available in your location.','ineligible':{}}})
        backend=web.Application(); backend.router.add_get('/',html)
        backend.router.add_post('/exa.language_server_pb.LanguageServerService/{method}',rpc)
        br,bp=await start(backend)
        app=create_app(config(tmp_path),supervisor=FakeSupervisor(bp)); sr,sp=await start(app)
        url='http://127.0.0.1:'+str(sp)
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as c:
                await login(c,url)
                result=await (await c.get(url+'/__suite/api/system/auth-status')).json()
                assert result['token_present'] is True and result['authorized'] is False
                assert result['error_kind']=='ineligible' and '地区' in result['reason']
        finally: await sr.cleanup(); await br.cleanup()
    asyncio.run(scenario())
