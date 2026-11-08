"""Tunnel only known read-only native subscriptions over authenticated WebSockets."""
import asyncio
import base64
from urllib.parse import urlsplit
import aiohttp
from aiohttp import web
from multidict import CIMultiDict

SUBSCRIPTIONS=frozenset(('JetboxSubscribeToState','JetboxSubscribeToSummaries','ProjectUpdatesStream','SubscribeToSidecars','SubscribeToMcpServerStates','SubscribeToPlugins'))
PREFIX='/exa.language_server_pb.LanguageServerService/'

async def subscription(request):
    state=request.app['state'];origin=request.headers.get('Origin','')
    if not origin or urlsplit(origin).netloc!=request.host:raise web.HTTPForbidden()
    if state.busy or not state.supervisor.active_port:raise web.HTTPServiceUnavailable()
    port=state.supervisor.active_port;profile=state.active
    ws=web.WebSocketResponse(max_msg_size=2*1024*1024,heartbeat=30);await ws.prepare(request)
    connections=getattr(state,'native_stream_sockets',None)
    if connections is None:connections=state.native_stream_sockets=set()
    connections.add(ws)
    upstream_task=closed=changed=None
    try:
        message=await asyncio.wait_for(ws.receive_json(),10)
        if not isinstance(message,dict) or message.get('method')!='POST':raise ValueError('Invalid subscription')
        method=message.get('subscription')
        if method not in SUBSCRIPTIONS:raise ValueError('Unsupported subscription')
        body=base64.b64decode(message.get('body',''),validate=True)
        if len(body)>1024*1024:raise ValueError('Subscription body exceeds limit')
        fields=message.get('headers',{})
        if not isinstance(fields,dict):raise ValueError('Invalid subscription headers')
        target='%s://127.0.0.1:%d'%(state.cfg.get('backend_scheme','https'),port)
        headers=CIMultiDict(Host='127.0.0.1:'+str(port),Origin=target)
        allowed={'content-type','connect-protocol-version','connect-timeout-ms','connect-content-encoding','connect-accept-encoding','grpc-encoding','grpc-accept-encoding','x-codeium-csrf-token','x-codeium-client-version','x-codeium-client-id','x-codeium-api-key'}
        for name,value in fields.items():
            if name.lower() in allowed and isinstance(value,str) and len(value)<8192:headers[name]=value
        if 'x-codeium-csrf-token' not in headers:raise ValueError('Missing native CSRF')
        from http.cookies import SimpleCookie
        cookies=SimpleCookie();cookies.load(request.headers.get('Cookie',''));prefix='agp_'+str(profile or 'none')+'_'
        native=[name[len(prefix):]+'='+value.coded_value for name,value in cookies.items() if name.startswith(prefix)]
        if native:headers['Cookie']='; '.join(native)
        headers['Accept-Encoding']='identity'
        async def forward():
            timeout=aiohttp.ClientTimeout(total=None,sock_connect=10);ssl=None if state.cfg.get('backend_verify_tls') else False
            client=getattr(state,'stream_http',None) or state.http
            async with client.post(target+PREFIX+method,data=body,headers=headers,ssl=ssl,timeout=timeout,auto_decompress=False) as upstream:
                response_headers={k:v for k,v in upstream.headers.items() if k.lower() in ('content-type','grpc-status','grpc-message','connect-protocol-version','connect-content-encoding','connect-accept-encoding','grpc-encoding')}
                await ws.send_json(dict(status=upstream.status,headers=response_headers))
                async for chunk in upstream.content.iter_any():
                    if state.active!=profile or state.supervisor.active_port!=port:break
                    await ws.send_bytes(chunk)
                await ws.send_json(dict(end=True))
        async def wait_runtime_change():
            while state.active==profile and state.supervisor.active_port==port and not state.busy:await asyncio.sleep(.5)
        upstream_task=asyncio.create_task(forward());closed=asyncio.create_task(ws.receive());changed=asyncio.create_task(wait_runtime_change())
        done,_=await asyncio.wait([upstream_task,closed,changed],return_when=asyncio.FIRST_COMPLETED)
        if upstream_task in done:await upstream_task
    except (aiohttp.ClientError,OSError,ValueError,TypeError,asyncio.TimeoutError):
        if not ws.closed:await ws.send_json(dict(error='可能是原生订阅连接中断，请返回面板检查运行状态'))
    finally:
        for task in (upstream_task,closed,changed):
            if task:task.cancel()
        await asyncio.gather(*[t for t in (upstream_task,closed,changed) if t],return_exceptions=True);await ws.close()
        connections.discard(ws)
    return ws
