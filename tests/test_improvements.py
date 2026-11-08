import asyncio
import base64
import json
import os
import tarfile
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

from test_suite import config, FakeSupervisor, start, login
from server import create_app
from profile_manager import ProfileManager
from proxy_manager import ProxyManager
from outbound_proxy import OutboundProxy


def test_csp_blocks_external_subresources_and_strips_ip_headers(tmp_path):
    async def scenario():
        captured={}
        async def html(req):
            captured.update(req.headers)
            return web.Response(text='<html><head><link rel="preconnect" href="https://fonts.google.com"></head><body>IDE</body></html>',content_type='text/html')
        backend=web.Application();backend.router.add_get('/',html)
        br,bp=await start(backend)
        sr,sp=await start(create_app(config(tmp_path),supervisor=FakeSupervisor(bp)))
        url='http://127.0.0.1:'+str(sp)
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as c:
                await login(c,url)
                response=await c.get(url+'/',headers={'X-Forwarded-For':'203.0.113.99','CF-Connecting-IP':'203.0.113.99','Forwarded':'for=203.0.113.99'})
                assert response.status==200
                policy=response.headers['Content-Security-Policy']
                assert "default-src 'self'" in policy and "connect-src 'self'" in policy
                assert 'google.com' not in policy and 'https:' not in policy
                assert response.headers['Referrer-Policy']=='no-referrer'
                assert not any(k.lower() in ['x-forwarded-for','cf-connecting-ip','forwarded'] for k in captured)
                text=await response.text()
                assert '/__suite/privacy.js' in text and 'preconnect' not in text
                assert text.index('/__suite/privacy.js')<text.index('IDE')
        finally:await sr.cleanup();await br.cleanup()
    asyncio.run(scenario())


def test_external_redirect_is_blocked_before_browser_receives_it(tmp_path):
    async def scenario():
        async def redirect(req):raise web.HTTPFound('https://www.google.com/')
        backend=web.Application();backend.router.add_get('/',redirect)
        br,bp=await start(backend);sr,sp=await start(create_app(config(tmp_path),supervisor=FakeSupervisor(bp)))
        url='http://127.0.0.1:'+str(sp)
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as c:
                await login(c,url)
                response=await c.get(url+'/',allow_redirects=False)
                assert response.status==403 and 'Location' not in response.headers
        finally:await sr.cleanup();await br.cleanup()
    asyncio.run(scenario())


@pytest.mark.skipif(os.name=='nt',reason='Linux deployment uses filesystem symlinks; run on target server')
def test_each_account_has_separate_home_cache_tmp_and_workspace(tmp_path):
    m=ProfileManager(tmp_path,tmp_path/'profiles')
    a=m.create_profile('A');b=m.create_profile('B')
    m.restore_profile(a['id']);home_a=m.home
    for name in ['.cache/private','workspace/private','.tmp/private']:
        p=home_a/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('account-A')
    m.save_active_to_profile(a['id'])
    m.restore_profile(b['id']);home_b=m.home
    assert home_a!=home_b and not (home_b/'workspace/private').exists()
    m.restore_profile(a['id'])
    assert m.home==home_a and (m.home/'workspace/private').read_text()=='account-A'
    file=tmp_path/'a.tar.gz';m.export_profile(a['id'],file)
    clone=m.import_profile(file)
    assert (m.home_for(clone['id'])/'workspace/private').read_text()=='account-A'

@pytest.mark.skipif(os.name=='nt',reason='Linux deployment uses filesystem symlinks; run on target server')
def test_conversation_logs_survive_switch_export_import_and_stale_snapshot(tmp_path):
    m=ProfileManager(tmp_path,tmp_path/'profiles')
    a=m.create_profile('A');b=m.create_profile('B')
    m.restore_profile(a['id'])
    transcript=m.home/'.gemini/antigravity/brain/session/.system_generated/logs/transcript.jsonl'
    transcript.parent.mkdir(parents=True);transcript.write_text('original conversation')
    m.save_active_to_profile(a['id'])
    assert (m.history.root/'brain/session/.system_generated/logs/transcript.jsonl').read_text()=='original conversation'
    transcript.write_text('new conversation after snapshot')
    m.restore_profile(b['id']);m.restore_profile(a['id'])
    assert transcript.read_text()=='new conversation after snapshot'
    assert (m.home/'.gemini/antigravity/brain/session/.system_generated/logs/transcript.jsonl').read_text()=='new conversation after snapshot'
    archive=tmp_path/'history.tar.gz';m.export_profile(a['id'],archive)
    clone=m.import_profile(archive);m.restore_profile(clone['id'])
    copy=m.home/'.gemini/antigravity/brain/session/.system_generated/logs/transcript.jsonl'
    assert copy.read_text()=='new conversation after snapshot'


def test_proxy_links_encoded_credentials_ipv6_and_atomic_rejection(tmp_path):
    m=ProxyManager(tmp_path)
    nodes=m.import_links('https://user:p%40ss@[2001:db8::1]:443#TLS\nsocks5h://localhost:1080#Socks\nlocalhost:8080')
    assert len(nodes)==3 and nodes[0]['password']=='p@ss'
    assert m.get_proxy_url(nodes[0])=='https://user:p%40ss@[2001:db8::1]:443'
    with pytest.raises(ValueError):m.import_links('http://good:8080\nss://unsupported')
    assert len(m.proxies)==3


def test_authenticated_http_proxy_bridge(tmp_path):
    async def scenario():
        captured={}
        async def echo(reader,writer):
            data=await reader.readexactly(5);writer.write(data.upper());await writer.drain();writer.close()
        target=await asyncio.start_server(echo,'127.0.0.1',0)
        target_port=target.sockets[0].getsockname()[1]
        async def proxy(reader,writer):
            first=await reader.readline();headers={}
            while True:
                line=await reader.readline()
                if line==b'\r\n':break
                key,value=line.decode().split(':',1);headers[key.lower()]=value.strip()
            captured.update(headers)
            remote,remote_writer=await asyncio.open_connection('127.0.0.1',target_port)
            writer.write(b'HTTP/1.1 200 Connection established\r\n\r\n');await writer.drain()
            data=await reader.readexactly(5);remote_writer.write(data);await remote_writer.drain()
            writer.write(await remote.readexactly(5));await writer.drain();writer.close();remote_writer.close()
        upstream=await asyncio.start_server(proxy,'127.0.0.1',0)
        try:
            port=upstream.sockets[0].getsockname()[1]
            async with OutboundProxy('http://user:p%40ss@127.0.0.1:'+str(port)) as bridge:
                reader,writer=await asyncio.open_connection('127.0.0.1',int(bridge.url.rsplit(':',1)[1]))
                writer.write(('CONNECT 127.0.0.1:%d HTTP/1.1\r\nHost: test\r\n\r\n'%target_port).encode());await writer.drain()
                assert b'200' in await reader.readline();assert await reader.readline()==b'\r\n'
                writer.write(b'hello');await writer.drain()
                assert await asyncio.wait_for(reader.readexactly(5),2)==b'HELLO'
                assert captured['proxy-authorization']=='Basic '+base64.b64encode(b'user:p@ss').decode()
                writer.close()
        finally:upstream.close();target.close();await upstream.wait_closed();await target.wait_closed()
    asyncio.run(scenario())


def test_socks5_node_check_and_runtime_resolve_domains_at_the_proxy(tmp_path,monkeypatch):
    from types import SimpleNamespace
    captured={}
    nodes=ProxyManager(tmp_path)
    node=nodes.add_proxy('SOCKS5','socks5','127.0.0.1',1080)
    def curl(command,**kwargs):
        captured['curl_proxy']=command[command.index('--proxy')+1]
        return SimpleNamespace(returncode=0,stdout='{"ip":"203.0.113.7"}',stderr='')
    monkeypatch.setattr('proxy_manager.subprocess.run',curl)
    assert nodes.test_proxy(node)['ok']
    assert captured['curl_proxy']=='socks5h://127.0.0.1:1080'
    async def scenario():
        received=asyncio.get_running_loop().create_future()
        async def socks(reader,writer):
            try:
                version,count=await reader.readexactly(2)
                assert version==5
                await reader.readexactly(count)
                writer.write(b'\x05\x00');await writer.drain()
                request=await reader.readexactly(4)
                assert request==b'\x05\x01\x00\x03'
                size=(await reader.readexactly(1))[0]
                domain=(await reader.readexactly(size)).decode()
                port=int.from_bytes(await reader.readexactly(2),'big')
                received.set_result((domain,port))
                writer.write(b'\x05\x00\x00\x01'+b'\x00'*6);await writer.drain()
                await reader.read()
            except Exception as error:
                if not received.done():received.set_exception(error)
            finally:writer.close()
        upstream=await asyncio.start_server(socks,'127.0.0.1',0)
        try:
            node['port']=upstream.sockets[0].getsockname()[1]
            async with OutboundProxy(nodes.get_proxy_url(node)) as bridge:
                _,writer=await bridge.connect('only-the-proxy-can-resolve.invalid',443)
                assert await asyncio.wait_for(received,3)==('only-the-proxy-can-resolve.invalid',443)
                writer.close();await writer.wait_closed()
        finally:
            upstream.close();await upstream.wait_closed()
    asyncio.run(scenario())


@pytest.mark.skipif(os.name=='nt',reason='Linux deployment uses filesystem symlinks; run on target server')
def test_start_endpoint_reports_starting_then_ready_without_redirect(tmp_path):
    async def scenario():
        app=create_app(config(tmp_path),supervisor=FakeSupervisor());state=app['state']
        p=state.profiles.create_profile('A')
        state.request_detection=lambda target:None
        real_activate=state.activate
        async def slow(target):await asyncio.sleep(.1);await real_activate(target)
        state.activate=slow
        sr,sp=await start(app);url='http://127.0.0.1:'+str(sp)
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as c:
                await login(c,url)
                response=await c.post(url+'/__suite/api/profiles/switch',json={'profile_id':p['id']})
                assert response.status==202 and 'Location' not in response.headers
                status=await (await c.get(url+'/__suite/api/status')).json()
                assert status['starting_profile_id']==p['id'] and not status['antigravity_ready']
                await state.launch_task
                status=await (await c.get(url+'/__suite/api/status')).json()
                assert status['starting_profile_id'] is None and status['antigravity_ready']
        finally:await sr.cleanup()
    asyncio.run(scenario())


def test_uninstall_only_managed_core_and_preserves_account_data(tmp_path):
    async def scenario():
        from core_installer import CoreInstaller
        cfg=config(tmp_path);root=tmp_path/'core';root.mkdir();binary=root/'antigravity';binary.write_text('binary')
        cfg['core_directory']=str(root)
        sup=FakeSupervisor();sup.antigravity_bin=str(binary)
        account=tmp_path/'profiles/token';account.parent.mkdir();account.write_text('keep')
        installer=CoreInstaller(cfg,sup);installer.start_uninstall();await installer.task
        assert installer.state['status']=='done' and not root.exists() and account.read_text()=='keep'
        shared=tmp_path/'shared';shared.mkdir();sup.antigravity_bin=str(shared/'antigravity')
        installer.start_uninstall();await installer.task
        assert installer.state['status']=='error' and shared.exists()
    asyncio.run(scenario())

def test_shared_summary_database_merges_all_conversation_ids(tmp_path):
    import sqlite3
    from shared_history import SharedHistory
    history=SharedHistory(tmp_path)
    for index,rows in enumerate([[('A','old',1)],[('B','second',2),('A','new',3)]]):
        file=tmp_path/('source-%d.db'%index)
        connection=sqlite3.connect(file)
        connection.execute('CREATE TABLE conversation_summaries (conversation_id TEXT PRIMARY KEY,title TEXT,last_modified_time INTEGER)')
        connection.executemany('INSERT INTO conversation_summaries VALUES (?,?,?)',rows);connection.commit();connection.close()
        history.merge_database(file)
    connection=sqlite3.connect(history.root/'conversation_summaries.db')
    assert connection.execute('SELECT conversation_id,title FROM conversation_summaries ORDER BY conversation_id').fetchall()==[('A','new'),('B','second')]
    connection.close()

def test_account_cookies_are_scoped_and_other_accounts_are_not_forwarded(tmp_path):
    async def scenario():
        received={}
        async def html(req):
            received['cookie']=req.headers.get('Cookie','')
            response=web.Response(text='<html><body>IDE</body></html>',content_type='text/html')
            response.set_cookie('native','cookie-value',httponly=True)
            return response
        backend=web.Application();backend.router.add_get('/',html);br,bp=await start(backend)
        app=create_app(config(tmp_path),supervisor=FakeSupervisor(bp));app['state'].active='accountA'
        sr,sp=await start(app);url='http://127.0.0.1:'+str(sp)
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                await login(client,url)
                response=await client.get(url+'/')
                assert 'agp_accountA_native' in response.cookies
                assert response.cookies['agp_accountA_native']['httponly']
                await client.get(url+'/')
                assert 'native=cookie-value' in received['cookie']
                app['state'].active='accountB'
                await client.get(url+'/')
                assert 'cookie-value' not in received['cookie'] and 'ag_session' not in received['cookie']
        finally:await sr.cleanup();await br.cleanup()
    asyncio.run(scenario())

@pytest.mark.skipif(os.name=='nt',reason='Linux deployment uses filesystem symlinks; run on target server')
def test_all_profiles_read_same_history_and_keep_distinct_credentials(tmp_path):
    manager=ProfileManager(tmp_path,tmp_path/'profiles')
    a=manager.create_profile('A');b=manager.create_profile('B')
    for profile,text in [(a,'token-A'),(b,'token-B')]:
        token=manager.profile_dir(profile['id'])/'store/jetski-standalone-oauth-token';token.write_text(text)
    manager.restore_profile(a['id'])
    chat=manager.home/'.gemini/antigravity/conversations/test.pb';chat.write_text('shared chat')
    manager.restore_profile(b['id'])
    assert (manager.home/'.gemini/antigravity/conversations/test.pb').read_text()=='shared chat'
    assert (manager.home/'.gemini/jetski-standalone-oauth-token').read_text()=='token-B'
    manager.delete_profile(a['id'])
    assert (manager.history.root/'conversations/test.pb').read_text()=='shared chat'
