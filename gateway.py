import asyncio
import re
from http.cookies import SimpleCookie

from aiohttp import ClientError, WSMsgType, web
from multidict import CIMultiDict
from yarl import URL
from privacy import CLIENT_IP_HEADERS, inject_privacy, privacy_headers
from logout_protection import blocks_logout,WARNING
from client_assets import ClientAssets,script_response

HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
       'te', 'trailer', 'transfer-encoding', 'upgrade'}


def clean_headers(headers):
    excluded = HOP | {h.strip().lower() for h in headers.get('Connection', '').split(',')}
    return CIMultiDict((k, v) for k, v in headers.items() if k.lower() not in excluded)


async def proxy(request):
    state = request.app['state']
    if blocks_logout(request.raw_path):return web.json_response(dict(ok=False,blocked_logout=True,error=WARNING),status=403)
    port = state.supervisor.active_port
    if state.busy or not port:
        if request.path == '/' and request.method == 'GET':
            return web.FileResponse(state.static / 'portal.html')
        return web.json_response({'ok': False, 'error': '核心引擎尚未就绪'}, status=503)
    scheme = state.cfg.get('backend_scheme', 'https')
    target = '%s://127.0.0.1:%d' % (scheme, port)
    url = URL(target + request.raw_path, encoded=True)
    headers = clean_headers(request.headers)
    for key in list(headers):
        if key.lower() in CLIENT_IP_HEADERS: headers.popall(key, None)
    headers['Host'] = '127.0.0.1:%d' % port
    for k in ['Origin', 'Referer', 'X-Forwarded-Host']:
        if k in headers: headers[k] = target + ('/' if k == 'Referer' else '')
    cookies = SimpleCookie()
    cookies.load(headers.get('Cookie', ''))
    cookie_prefix='agp_'+str(state.active or 'none')+'_'
    native=[k[len(cookie_prefix):]+'='+v.coded_value for k,v in cookies.items() if k.startswith(cookie_prefix)]
    if native: headers['Cookie'] = '; '.join(native)
    else: headers.pop('Cookie', None)
    ssl_option = None if state.cfg.get('backend_verify_tls') else False
    if request.headers.get('Upgrade', '').lower() == 'websocket':
        return await websocket(request, url, headers, ssl_option)
    inject = request.method == 'GET' and (request.path == '/' or 'text/html' in request.headers.get('Accept', ''))
    client_script=request.method=='GET' and request.path=='/main.js'
    if inject:headers['Accept-Encoding']='identity'
    if client_script:
        headers['Accept-Encoding']='gzip'
        # Browser validators refer to the guarded body, never the original vendor script.
        for key in ('If-None-Match','If-Modified-Since','Range','If-Range'):headers.popall(key,None)
    body = request.content.iter_chunked(65536) if request.can_read_body else None
    response = None
    try:
        async with state.http.request(request.method, url, headers=headers, data=body,
                                      ssl=ssl_option, allow_redirects=False,
                                      auto_decompress=inject or client_script) as upstream:
            out = clean_headers(upstream.headers)
            out['X-Accel-Buffering']='no'
            out.popall('Link',None)  # Prevent origin-supplied external preconnect/preload hints.
            location=out.get('Location')
            if location:
                destination=URL(target).join(URL(location))
                if destination.origin()!=URL(target).origin():
                    return web.json_response({'ok':False,'error':'隐私保护已阻止外部跳转，请在配置大厅授权账号'},status=403)
                out['Location']=destination.raw_path_qs
            out.popall('Refresh',None)
            if client_script and upstream.status==200:
                chunks=bytearray()
                async for chunk in upstream.content.iter_chunked(65536):
                    chunks.extend(chunk)
                    if len(chunks)>32*1024*1024:raise ValueError('客户端脚本超过大小限制')
                bootstrap=(state.static/'logout-guard.js').read_text(encoding='utf-8')
                cache=getattr(state,'client_assets',None)
                if cache is None:cache=state.client_assets=ClientAssets()
                process=state.supervisor.process
                context=(state.active,port,getattr(process,'pid',None),getattr(process,'identity',None))
                entry=await cache.guarded_script(bytes(chunks),bootstrap,context)
                state.session_ops.desktop_status['web_verified']=entry['supported']
                return script_response(request,entry,out)
            # Namespace browser cookies too; switching profiles must not reuse them.
            values=out.popall('Set-Cookie',[])
            for value in values:
                parsed_cookie=SimpleCookie();parsed_cookie.load(value)
                for name,morsel in parsed_cookie.items():
                    if name=='ag_session':continue
                    renamed=SimpleCookie();renamed[cookie_prefix+name]=morsel.value
                    for attr,attribute_value in morsel.items():
                        if attr!='domain' and attribute_value:renamed[cookie_prefix+name][attr]=attribute_value
                    out.add('Set-Cookie',renamed[cookie_prefix+name].OutputString())
            if inject and upstream.status == 200 and 'text/html' in out.get('Content-Type', ''):
                chunks = bytearray()
                async for chunk in upstream.content.iter_chunked(65536):
                    chunks.extend(chunk)
                    if len(chunks) > 16 * 1024 * 1024: raise ValueError('IDE 首页超过大小限制')
                data = bytes(chunks)
                version=(state.static.parent/'VERSION').read_text(encoding='utf-8').strip()
                # Old cached client bundles may have only the request-level fallback guard.
                # Version the native entry script so a refresh loads the full action guard.
                data=re.sub(br'(<script\b[^>]*\bsrc=["\x27])(/main\.js)(?:\?[^"\x27]*)?(["\x27])',
                    lambda match:match[1]+match[2]+b'?agy_suite='+version.encode()+match[3],data,flags=re.I)
                data = inject_privacy(data, state.active)
                scripts=b'<script src="/__suite/stream-transport.js"></script><script src="/__suite/ide-ready.js"></script>'
                head=re.search(br'<head\b[^>]*>',data,re.I)
                data=data[:head.end()]+scripts+data[head.end():] if head else scripts+data
                guard=b'<script src="/__suite/logout-guard.js"></script>'
                if guard not in data:
                    head=re.search(br'<head\b[^>]*>',data,re.I)
                    data=data[:head.end()]+guard+data[head.end():] if head else guard+data
                marker = b'<script src="/__suite/floating.js"></script>'
                if marker not in data:
                    pos = data.lower().rfind(b'</body>')
                    data = data[:pos] + marker + data[pos:] if pos >= 0 else data + marker
                for k in ['Content-Length', 'Content-Encoding', 'ETag', 'Content-MD5']:
                    out.popall(k, None)
                out['Cache-Control'] = 'no-store'
                return web.Response(body=data, status=upstream.status, headers=out)
            response = web.StreamResponse(status=upstream.status, headers=out)
            response.headers.update(privacy_headers(request))
            await response.prepare(request)
            async for chunk in upstream.content.iter_any():
                await response.write(chunk)
            await response.write_eof()
            return response
    except (ClientError, OSError, asyncio.TimeoutError):
        if response is not None and response.prepared:
            if request.transport: request.transport.close()
            return response
        return web.json_response({'ok': False, 'error': '无法连接核心引擎'}, status=502)


async def websocket(request, url, headers, ssl_option):
    state = request.app['state']
    protocols = [p.strip() for p in request.headers.get('Sec-WebSocket-Protocol', '').split(',') if p.strip()]
    for k in ['Sec-WebSocket-Key', 'Sec-WebSocket-Version', 'Sec-WebSocket-Extensions', 'Sec-WebSocket-Protocol']:
        headers.popall(k, None)
    try:
        remote = await state.http.ws_connect(url, headers=headers, protocols=protocols, ssl=ssl_option,
                                            autoping=False, max_msg_size=0)
    except (ClientError, OSError):
        return web.json_response({'ok': False, 'error': 'WebSocket 连接失败'}, status=502)
    client = web.WebSocketResponse(protocols=[remote.protocol] if remote.protocol else [],
                                   autoping=False, max_msg_size=0)
    await client.prepare(request)
    async def relay(source, dest):
        async for msg in source:
            if msg.type == WSMsgType.TEXT: await dest.send_str(msg.data)
            elif msg.type == WSMsgType.BINARY: await dest.send_bytes(msg.data)
            elif msg.type == WSMsgType.PING: await dest.ping(msg.data)
            elif msg.type == WSMsgType.PONG: await dest.pong(msg.data)
            else: break
    tasks = [asyncio.create_task(relay(client, remote)), asyncio.create_task(relay(remote, client))]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await remote.close()
        await client.close()
    return client
