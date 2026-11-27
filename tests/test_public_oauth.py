import asyncio

import aiohttp

from server import create_app
from test_suite import config, FakeSupervisor, start, login


def test_public_build_requires_explicit_oauth_application(tmp_path, monkeypatch):
    monkeypatch.delenv('ANTIGRAVITY_OAUTH_CLIENT_ID', raising=False)
    async def scenario():
        cfg = config(tmp_path)
        cfg.pop('oauth_client_id')
        runner, port = await start(create_app(cfg, supervisor=FakeSupervisor()))
        url = 'http://127.0.0.1:%d' % port
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                await login(client, url)
                response = await client.post(url + '/__suite/api/auth/google/start')
                assert response.status == 400
                assert '本机官方客户端' in (await response.json())['error']
                assert 'docs/oauth.md' in (await response.json())['error']
        finally:
            await runner.cleanup()
    asyncio.run(scenario())


def test_registration_is_read_from_local_binary_without_bundled_secret(tmp_path, monkeypatch):
    import hashlib
    import oauth_registration as module
    monkeypatch.delenv('ANTIGRAVITY_OAUTH_CLIENT_ID', raising=False)
    monkeypatch.delenv('ANTIGRAVITY_OAUTH_CLIENT_SECRET', raising=False)
    secret = 'example-test-registration-value'
    monkeypatch.setattr(module, 'SECRET_FINGERPRINT', hashlib.sha256(secret.encode()).hexdigest())
    monkeypatch.setattr(module, 'SECRET_LENGTH', len(secret))
    monkeypatch.setattr(module, 'DESKTOP_CLIENT_ID', 'example.apps.googleusercontent.com')
    # The scanner intentionally recognizes the vendor prefix, but the fixture uses
    # a mock mmap object so it never embeds an actual vendor credential in Git.
    binary = tmp_path / 'resources/bin/language_server'
    binary.parent.mkdir(parents=True)
    payload = module.DESKTOP_CLIENT_ID.encode() + b'\0' + secret.encode()
    binary.write_bytes(payload)
    class MockMap:
        def __init__(self, value): self.value=value
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def __contains__(self, key): return key in self.value
        def __getitem__(self, key): return self.value[key]
        def find(self, key, offset=0):
            if key==module.DESKTOP_CLIENT_ID.encode():return self.value.find(key,offset)
            return self.value.find(secret.encode(), offset)
    monkeypatch.setattr(module.mmap, 'mmap', lambda *args, **kwargs: MockMap(payload))
    module._read_registration.cache_clear()
    assert module.resolve_registration({'antigravity_bin': str(tmp_path / 'antigravity')}) == (module.DESKTOP_CLIENT_ID, secret)
    module._read_registration.cache_clear()


def test_oauth_complete_defaults_blank_name_to_email_and_binds_proxy(tmp_path, monkeypatch):
    import oauth as oauth_module

    class FakeResponse:
        def __init__(self, payload, status=200):
            self.payload = payload
            self.status = status
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): pass
        async def json(self): return self.payload

    class FakeClientSession:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): pass
        def post(self, url, data=None, proxy=None):
            assert 'oauth2.googleapis.com/token' in url
            assert proxy and proxy.startswith('http://127.0.0.1:')
            return FakeResponse({'access_token': 'acc-1', 'refresh_token': 'ref-1', 'expires_in': 3600})
        def get(self, url, headers=None, proxy=None):
            assert 'userinfo' in url
            assert proxy and proxy.startswith('http://127.0.0.1:')
            return FakeResponse({'email': 'oauth-user@example.invalid'})

    from types import SimpleNamespace
    monkeypatch.setattr(oauth_module, 'aiohttp', SimpleNamespace(
        ClientSession=FakeClientSession,
        ClientTimeout=aiohttp.ClientTimeout,
    ))

    async def scenario():
        app = create_app(config(tmp_path), supervisor=FakeSupervisor())
        state = app['state']
        state.request_detection = lambda target: None
        node = state.proxies.add_proxy('住宅节点', 'socks5', '198.51.100.1', 1080)
        runner, port = await start(app)
        url = 'http://127.0.0.1:%d' % port
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                await login(client, url)
                started = await (await client.post(url + '/__suite/api/auth/google/start')).json()
                assert started['ok'] and started['auth_url'].startswith('https://accounts.google.com/o/oauth2/v2/auth?')
                callback = started['redirect_uri'] + '?state=' + started['state'] + '&code=4/0A-test-code'
                resp = await client.post(url + '/__suite/api/auth/google/complete', json={
                    'code': callback, 'state': started['state'], 'name': '', 'proxy_id': node['id']
                })
                assert resp.status == 200
                body = await resp.json()
                assert body['ok'] and body['email'] == 'oauth-user@example.invalid'
                assert body['profile']['name'] == 'oauth-user@example.invalid'
                assert body['profile']['proxy_id'] == node['id']
        finally:
            await runner.cleanup()
    asyncio.run(scenario())
