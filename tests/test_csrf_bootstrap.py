import asyncio
from types import SimpleNamespace

import aiohttp
from aiohttp import web

import process_tracking
from auth_probe import auth_status
from gateway import inject_csrf_token
from server import create_app
from test_suite import FakeProcess, FakeSupervisor, config, login, start


class BootstrapSupervisor(FakeSupervisor):
    def __init__(self, port, token='admin-csrf-token'):
        super().__init__(port)
        self.process = FakeProcess()
        self._token = token

    def csrf_token(self):
        return self._token


def test_inject_csrf_token_replaces_only_empty_value():
    html = b'<script>window.__APP_CONFIG__={"csrfToken":"","appVersion":"2.34.0"};</script>'
    updated = inject_csrf_token(html, 'session-tok-123')
    assert b'"csrfToken":"session-tok-123"' in updated
    untouched = b'<script>window.__APP_CONFIG__={"csrfToken":"already-set"};</script>'
    assert inject_csrf_token(untouched, 'session-tok-123') == untouched


def test_gateway_mints_and_injects_session_csrf_for_v234_empty_token(tmp_path):
    async def scenario():
        calls = []

        async def html(req):
            return web.Response(
                text='<html><head><script>window.__APP_CONFIG__={"productName":"antigravity","csrfToken":"","appVersion":"2.34.0"};</script></head><body></body></html>',
                content_type='text/html',
            )

        async def mint(req):
            calls.append(('mint', req.headers.get('x-codeium-csrf-token')))
            assert req.headers.get('x-codeium-csrf-token') == 'admin-csrf-token'
            return web.json_response({'bootstrapToken': 'boot-1'})

        async def auth(req):
            body = await req.json()
            calls.append(('auth', body.get('bootstrapToken'), req.headers.get('Origin')))
            assert body.get('bootstrapToken') == 'boot-1'
            assert req.headers.get('Origin', '').startswith('http://127.0.0.1:')
            return web.json_response({'sessionToken': 'minted-session-token'})

        backend = web.Application()
        backend.router.add_get('/', html)
        backend.router.add_post('/auth/mint', mint)
        backend.router.add_post('/auth', auth)
        br, bp = await start(backend)
        app = create_app(config(tmp_path), supervisor=BootstrapSupervisor(bp, 'admin-csrf-token'))
        sr, sp = await start(app)
        url = 'http://127.0.0.1:' + str(sp)
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                await login(client, url)
                resp = await client.get(url + '/')
                assert resp.status == 200
                text = await resp.text()
                assert '"csrfToken":"minted-session-token"' in text
                assert len(calls) == 2 and calls[0][0] == 'mint' and calls[1][0] == 'auth'
        finally:
            await sr.cleanup()
            await br.cleanup()

    asyncio.run(scenario())


def test_gateway_falls_back_to_admin_csrf_when_mint_unavailable(tmp_path):
    async def scenario():
        async def html(req):
            return web.Response(
                text='<html><head><script>window.__APP_CONFIG__={"csrfToken":""};</script></head><body></body></html>',
                content_type='text/html',
            )

        async def mint_missing(req):
            return web.Response(status=404)

        backend = web.Application()
        backend.router.add_get('/', html)
        backend.router.add_post('/auth/mint', mint_missing)
        br, bp = await start(backend)
        app = create_app(config(tmp_path), supervisor=BootstrapSupervisor(bp, 'admin-fallback-token'))
        sr, sp = await start(app)
        url = 'http://127.0.0.1:' + str(sp)
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                await login(client, url)
                resp = await client.get(url + '/')
                assert resp.status == 200
                assert '"csrfToken":"admin-fallback-token"' in await resp.text()
        finally:
            await sr.cleanup()
            await br.cleanup()

    asyncio.run(scenario())


def test_gateway_preserves_non_empty_csrf_without_calling_mint(tmp_path):
    async def scenario():
        mint_called = False

        async def html(req):
            return web.Response(
                text='<html><head><script>window.__APP_CONFIG__={"csrfToken":"legacy-csrf"};</script></head><body></body></html>',
                content_type='text/html',
            )

        async def mint(req):
            nonlocal mint_called
            mint_called = True
            return web.json_response({'bootstrapToken': 'should-not-run'})

        backend = web.Application()
        backend.router.add_get('/', html)
        backend.router.add_post('/auth/mint', mint)
        br, bp = await start(backend)
        app = create_app(config(tmp_path), supervisor=BootstrapSupervisor(bp, 'admin-csrf-token'))
        sr, sp = await start(app)
        url = 'http://127.0.0.1:' + str(sp)
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                await login(client, url)
                resp = await client.get(url + '/')
                assert resp.status == 200
                assert '"csrfToken":"legacy-csrf"' in await resp.text()
                assert not mint_called
        finally:
            await sr.cleanup()
            await br.cleanup()

    asyncio.run(scenario())


def test_auth_probe_falls_back_to_supervisor_csrf_token_when_page_token_empty():
    async def scenario():
        seen_headers = []

        async def backend(request):
            if request.path == '/':
                return web.Response(text='{"csrfToken":""}')
            seen_headers.append(request.headers.get('x-codeium-csrf-token'))
            if request.path.endswith('HasAuthToken'):
                return web.json_response(dict(hasToken=True))
            return web.json_response(dict(authResult=dict(hasValidAuth=True)))

        app = web.Application()
        app.router.add_route('*', '/{tail:.*}', backend)
        runner, port = await start(app)
        try:
            async with aiohttp.ClientSession() as client:
                supervisor = SimpleNamespace(active_port=port, csrf_token=lambda: 'env-csrf-token')
                state = SimpleNamespace(
                    cfg=dict(backend_scheme='http', auth_probe_timeout=2),
                    supervisor=supervisor,
                    active='dummy',
                    busy=False,
                    http=client,
                )
                result = await auth_status(state, force=True)
                assert result['authorized'] is True and result['token_present'] is True
                assert seen_headers == ['env-csrf-token', 'env-csrf-token']
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


def test_process_csrf_token_reads_language_server_env_and_argv(monkeypatch):
    fake_ls = dict(pid=200, ppid=100, start_time='10', state='S', uid=1000,
                   executable='/opt/antigravity/resources/bin/language_server',
                   args=['language_server'], env={'ANTIGRAVITY_SERVER_CSRF_TOKEN': 'ls-env-tok'})
    fake_main = dict(pid=100, ppid=1, start_time='1', state='S', uid=1000,
                     executable='/opt/antigravity/antigravity', args=['antigravity'], env={})
    monkeypatch.setattr(process_tracking, 'descendants', lambda pid: [fake_ls])
    monkeypatch.setattr(process_tracking, 'process_info', lambda pid: fake_main)
    assert process_tracking.process_csrf_token(100) == 'ls-env-tok'

    fake_ls_argv = dict(pid=201, ppid=100, start_time='11', state='S', uid=1000,
                        executable='/opt/antigravity/resources/bin/language_server',
                        args=['language_server', '--csrf_token', 'argv-tok'], env={})
    monkeypatch.setattr(process_tracking, 'descendants', lambda pid: [fake_ls_argv])
    monkeypatch.setattr(process_tracking, 'process_environment', lambda info, keys: {})
    assert process_tracking.process_csrf_token(100) == 'argv-tok'
