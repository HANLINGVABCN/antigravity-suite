import asyncio
import logging
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

from server import create_app
from test_suite import config, FakeSupervisor, start, login


class RefreshableSupervisor(FakeSupervisor):
    def __init__(self, initial_port, refreshed_port):
        super().__init__(initial_port)
        self._refreshed_port = refreshed_port
        self.refresh_calls = 0

    def refresh_runtime(self, adopt=True):
        self.refresh_calls += 1
        self.active_port = self._refreshed_port


def test_proxy_recovers_when_upstream_port_changes(tmp_path):
    async def scenario():
        async def ok_page(request):
            return web.Response(text='<html><head></head><body>ok-new-port</body></html>', content_type='text/html')

        async def ws_echo(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await ws.send_str('echo:' + msg.data)
                    break
            await ws.close()
            return ws

        # Allocate a closed port as the stale upstream, then start the live backend on a fresh port.
        stale_app = web.Application()
        stale_runner, stale_port = await start(stale_app)
        await stale_runner.cleanup()

        live_backend = web.Application()
        live_backend.router.add_get('/c/test', ok_page)
        live_backend.router.add_get('/connect-websocket', ws_echo)
        br, live_port = await start(live_backend)

        supervisor = RefreshableSupervisor(stale_port, live_port)
        app = create_app(config(tmp_path), supervisor=supervisor)
        app['state'].active = 'A'
        runner, port = await start(app)
        origin = 'http://127.0.0.1:' + str(port)
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                await login(client, origin)
                async with client.get(origin + '/c/test', headers={'Accept': 'text/html'}) as resp:
                    body = await resp.text()
                    assert resp.status == 200
                    assert 'ok-new-port' in body
                    assert '/__suite/ide-ready.js' in body
                assert supervisor.refresh_calls >= 1
                assert supervisor.active_port == live_port

                # Reset to stale port again to verify WebSocket auto-refresh & reconnect.
                supervisor.active_port = stale_port
                async with client.ws_connect(origin + '/connect-websocket', headers={'Origin': origin}) as ws:
                    await ws.send_str('ping')
                    reply = await ws.receive_str()
                    assert reply == 'echo:ping'
                assert supervisor.active_port == live_port
        finally:
            await runner.cleanup()
            await br.cleanup()

    asyncio.run(scenario())


def test_ide_ready_and_stream_transport_support_235_connect_websocket():
    root = Path(__file__).resolve().parent.parent
    ide_ready = (root / 'static/ide-ready.js').read_text(encoding='utf-8')
    stream_transport = (root / 'static/stream-transport.js').read_text(encoding='utf-8')
    assert 'ConnectWebSocket' in ide_ready
    assert 'data-dismiss' in ide_ready
    assert 'hasMountedRoot' in ide_ready
    assert 'JetboxSubscribeToState' in ide_ready
    assert '/connect-websocket' in stream_transport
    assert 'ConnectWebSocket' in stream_transport


def test_stream_transport_logs_warning_on_error(tmp_path, caplog):
    async def scenario():
        backend = web.Application()
        br, bp = await start(backend)
        app = create_app(config(tmp_path), supervisor=FakeSupervisor(bp))
        app['state'].active = 'A'
        runner, port = await start(app)
        origin = 'http://127.0.0.1:' + str(port)
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                await login(client, origin)
                with caplog.at_level(logging.WARNING):
                    async with client.ws_connect(origin + '/__suite/native-stream', headers={'Origin': origin}) as ws:
                        await ws.send_json({'method': 'POST', 'subscription': 'AuthLogout', 'body': '', 'headers': {}})
                        msg = await ws.receive_json()
                        assert 'error' in msg
                assert any('原生订阅' in rec.message for rec in caplog.records)
        finally:
            await runner.cleanup()
            await br.cleanup()

    asyncio.run(scenario())
