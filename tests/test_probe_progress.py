import asyncio
from types import SimpleNamespace

import aiohttp
from aiohttp import web

from auth_probe import auth_status
from test_suite import start


def test_auth_probe_retries_transient_permission_timeout_and_preserves_stage():
    async def scenario():
        calls=[];progress=[]
        async def backend(request):
            if request.path=='/':return web.Response(text='{"csrfToken":"dummy-csrf"}')
            method=request.path.rsplit('/',1)[1];calls.append(method)
            if method=='HasAuthToken':return web.json_response(dict(hasToken=True))
            if calls.count('GetAuthStatus')==1:await asyncio.sleep(.2)
            return web.json_response(dict(authResult=dict(hasValidAuth=True)))
        app=web.Application();app.router.add_route('*','/{tail:.*}',backend);runner,port=await start(app)
        try:
            async with aiohttp.ClientSession() as client:
                state=SimpleNamespace(cfg=dict(backend_scheme='http',auth_probe_timeout=.05),supervisor=SimpleNamespace(active_port=port),active='dummy',busy=False,http=client)
                result=await auth_status(state,True,progress.append)
                assert result['authorized'] and result['probe_attempts']==2 and result['probe_stage']=='GetAuthStatus'
                assert any(p['attempt']==2 for p in progress) and calls.count('GetAuthStatus')==2
        finally:await runner.cleanup()
    asyncio.run(scenario())


def test_permission_timeout_keeps_known_token_presence_and_cannot_claim_node_failure():
    async def scenario():
        async def backend(request):
            if request.path=='/':return web.Response(text='{"csrfToken":"dummy-csrf"}')
            if request.path.endswith('HasAuthToken'):return web.json_response(dict(hasToken=True))
            await asyncio.sleep(.2)
            return web.json_response(dict(authResult=dict(hasValidAuth=True)))
        app=web.Application();app.router.add_route('*','/{tail:.*}',backend);runner,port=await start(app)
        try:
            async with aiohttp.ClientSession() as client:
                state=SimpleNamespace(cfg=dict(backend_scheme='http',auth_probe_timeout=.05),supervisor=SimpleNamespace(active_port=port),active='dummy',busy=False,http=client)
                result=await auth_status(state,True)
                assert not result['authorized'] and result['token_present'] is True
                assert result['error_kind']=='probe_timeout' and result['probe_stage']=='GetAuthStatus'
                assert '暂未确认账号是否可用' in result['reason'] and '可能' in result['reason']
        finally:await runner.cleanup()
    asyncio.run(scenario())


def test_auth_probe_reports_local_http_failure_without_exporting_response():
    async def scenario():
        async def backend(request):return web.Response(text='private-test-body',status=403)
        app=web.Application();app.router.add_route('*','/{tail:.*}',backend);runner,port=await start(app)
        try:
            async with aiohttp.ClientSession() as client:
                state=SimpleNamespace(cfg=dict(backend_scheme='http'),supervisor=SimpleNamespace(active_port=port),active='dummy',busy=False,http=client)
                result=await auth_status(state,True)
                assert result['probe_stage']=='page' and result['probe_http_status']==403
                assert result['probe_attempts']==1 and 'HTTP 403' in result['reason']
                assert 'private-test-body' not in str(result)
        finally:await runner.cleanup()
    asyncio.run(scenario())
