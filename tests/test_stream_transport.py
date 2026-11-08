import asyncio
import base64
import json

import aiohttp
import pytest
from aiohttp import web

from server import create_app
from stream_transport import PREFIX
from test_suite import config,FakeSupervisor,start,login


def test_subscription_bridge_preserves_wire_chunks_and_rejects_arbitrary_rpc(tmp_path):
    async def scenario():
        received=[]
        async def stream(request):
            received.append(dict(path=request.path,headers=dict(request.headers),body=await request.read()))
            response=web.StreamResponse(headers={'Content-Type':'application/connect+proto'});await response.prepare(request)
            await response.write(b'first-frame');await response.write(b'second-frame');await response.write_eof();return response
        backend=web.Application();backend.router.add_post(PREFIX+'JetboxSubscribeToSummaries',stream);br,bp=await start(backend)
        app=create_app(config(tmp_path),supervisor=FakeSupervisor(bp));app['state'].active='A'
        runner,port=await start(app);origin='http://127.0.0.1:'+str(port);url=origin+'/__suite/native-stream'
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                with pytest.raises(aiohttp.WSServerHandshakeError):await client.ws_connect(url,headers={'Origin':origin})
                await login(client,origin)
                with pytest.raises(aiohttp.WSServerHandshakeError):await client.ws_connect(url,headers={'Origin':'https://evil.invalid'})
                envelope=dict(method='POST',subscription='JetboxSubscribeToSummaries',body=base64.b64encode(b'fixture').decode(),headers={'Content-Type':'application/connect+proto','x-codeium-csrf-token':'dummy','Cookie':'secret','X-Forwarded-For':'203.0.113.1','Connect-Content-Encoding':'gzip'})
                async with client.ws_connect(url,headers={'Origin':origin}) as ws:
                    await ws.send_json(envelope);meta=await ws.receive_json();assert meta['status']==200
                    content=b''
                    async for message in ws:
                        if message.type==aiohttp.WSMsgType.BINARY:content+=message.data
                        elif message.type==aiohttp.WSMsgType.TEXT and json.loads(message.data).get('end'):break
                    assert content==b'first-framesecond-frame'
                assert received[0]['body']==b'fixture'
                assert 'secret' not in str(received[0]['headers']) and 'X-Forwarded-For' not in received[0]['headers']
                assert received[0]['headers']['Connect-Content-Encoding']=='gzip'
                async with client.ws_connect(url,headers={'Origin':origin}) as ws:
                    envelope['subscription']='AuthLogout';await ws.send_json(envelope);error=await ws.receive_json();assert 'error' in error
                assert len(received)==1
        finally:await runner.cleanup();await br.cleanup()
    asyncio.run(scenario())


def test_six_live_subscriptions_do_not_prevent_normal_rpc_and_close_on_disconnect(tmp_path):
    async def scenario():
        closed=0;hold=asyncio.Event()
        async def stream(request):
            nonlocal closed
            response=web.StreamResponse(headers={'Content-Type':'application/connect+proto'});await response.prepare(request)
            try:
                await response.write(b'first')
                for _ in range(50):await asyncio.sleep(.02);await response.write(b'heartbeat')
            except (ConnectionError,asyncio.CancelledError):pass
            finally:closed+=1
            return response
        async def rpc(request):return web.json_response(dict(hasToken=True))
        backend=web.Application();backend.router.add_post(PREFIX+'JetboxSubscribeToState',stream);backend.router.add_post(PREFIX+'HasAuthToken',rpc);br,bp=await start(backend)
        app=create_app(config(tmp_path),supervisor=FakeSupervisor(bp));runner,port=await start(app);origin='http://127.0.0.1:'+str(port)
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                await login(client,origin);sockets=[]
                try:
                    for _ in range(6):
                        ws=await client.ws_connect(origin+'/__suite/native-stream',headers={'Origin':origin});sockets.append(ws)
                        await ws.send_json(dict(method='POST',subscription='JetboxSubscribeToState',body='',headers={'x-codeium-csrf-token':'dummy'}));assert (await ws.receive_json())['status']==200
                    response=await asyncio.wait_for(client.post(origin+PREFIX+'HasAuthToken',json={}),1)
                    assert response.status==200 and (await response.json())['hasToken']
                finally:
                    for ws in sockets:await ws.close()
                for _ in range(60):
                    if closed==6:break
                    await asyncio.sleep(.02)
                assert closed==6
        finally:await runner.cleanup();await br.cleanup()
    asyncio.run(scenario())
