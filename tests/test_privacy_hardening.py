import asyncio
from pathlib import Path

import aiohttp
from aiohttp import web

from logout_protection import blocks_ws_logout
from privacy import NORMALIZED_ACCEPT_LANGUAGE, NORMALIZED_USER_AGENT
from server import create_app
from test_suite import config, FakeSupervisor, start, login


def test_fingerprint_normalization_and_telemetry_silencing(tmp_path):
    async def scenario():
        seen = []

        async def echo_headers(request):
            seen.append(dict(request.headers))
            return web.json_response({'ok': True})

        async def telemetry(request):
            seen.append({'telemetry_leaked': True})
            return web.json_response({'leaked': True})

        backend = web.Application()
        backend.router.add_get('/api/check', echo_headers)
        backend.router.add_post('/exa.language_server_pb.LanguageServerService/RecordAnalyticsEvent', telemetry)
        backend.router.add_post('/exa.language_server_pb.LanguageServerService/RecordObservabilityData', telemetry)
        br, bp = await start(backend)

        app = create_app(config(tmp_path), supervisor=FakeSupervisor(bp))
        app['state'].active = 'A'
        runner, port = await start(app)
        origin = 'http://127.0.0.1:' + str(port)
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                await login(client, origin)
                mobile_headers = {
                    'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) EdgA/157.0.0.0',
                    'Accept-Language': 'zh-CN,zh;q=0.9',
                    'Sec-CH-UA': '"Chromium";v="157"',
                    'Sec-CH-UA-Platform': '"Android"',
                    'Sec-CH-UA-Mobile': '?1',
                    'X-Forwarded-For': '203.0.113.9',
                }
                async with client.get(origin + '/api/check', headers=mobile_headers) as resp:
                    assert resp.status == 200
                assert len(seen) == 1
                hdrs = {k.lower(): v for k, v in seen[0].items()}
                assert hdrs['user-agent'] == NORMALIZED_USER_AGENT
                assert hdrs['accept-language'] == NORMALIZED_ACCEPT_LANGUAGE
                assert 'sec-ch-ua' not in hdrs
                assert 'sec-ch-ua-platform' not in hdrs
                assert 'sec-ch-ua-mobile' not in hdrs
                assert 'x-forwarded-for' not in hdrs

                # Telemetry endpoints must be silenced locally and never reach upstream.
                for path in (
                    '/exa.language_server_pb.LanguageServerService/RecordAnalyticsEvent',
                    '/exa.language_server_pb.LanguageServerService/RecordObservabilityData',
                ):
                    async with client.post(origin + path, json={'event': 'click'}, headers=mobile_headers) as resp:
                        assert resp.status == 200
                        assert await resp.json() == {}
                assert len(seen) == 1
        finally:
            await runner.cleanup()
            await br.cleanup()

    asyncio.run(scenario())


def test_connect_websocket_blocks_auth_logout_frames(tmp_path):
    assert blocks_ws_logout(b'\x00/exa.language_server_pb.LanguageServerService/AuthLogout')
    assert not blocks_ws_logout(b'hello AuthLogout discussion')

    async def scenario():
        received = []

        async def ws_backend(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    received.append(msg.data)
                    await ws.send_str('ack:' + msg.data)
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    received.append(msg.data)
                    await ws.send_bytes(b'ack:' + msg.data)
            await ws.close()
            return ws

        backend = web.Application()
        backend.router.add_get('/connect-websocket', ws_backend)
        br, bp = await start(backend)

        app = create_app(config(tmp_path), supervisor=FakeSupervisor(bp))
        app['state'].active = 'A'
        runner, port = await start(app)
        origin = 'http://127.0.0.1:' + str(port)
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                await login(client, origin)
                async with client.ws_connect(origin + '/connect-websocket', headers={'Origin': origin}) as ws:
                    await ws.send_str('/exa.language_server_pb.LanguageServerService/AuthLogout')
                    await ws.send_bytes(b'\x00/exa.language_server_pb.LanguageServerService/AuthLogout\x00')
                    await ws.send_str('normal-message')
                    reply = await ws.receive_str()
                    assert reply == 'ack:normal-message'
                assert received == ['normal-message']
        finally:
            await runner.cleanup()
            await br.cleanup()

    asyncio.run(scenario())


def test_privacy_js_scopes_broadcast_channel_and_disables_moz_rtc():
    root = Path(__file__).resolve().parent.parent
    pjs = (root / 'static/privacy.js').read_text(encoding='utf-8')
    assert 'mozRTCPeerConnection' in pjs
    assert 'BroadcastChannel' in pjs
    assert 'ScopedBC' in pjs
