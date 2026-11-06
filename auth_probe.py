import asyncio
import json
import re
import time


async def auth_status(state, force=False):
    port = state.supervisor.active_port
    if state.busy or not port:
        return dict(authorized=False, token_present=False, error_kind='not_ready', reason='请先启动一个档案')
    key = (state.active, port)
    cached = getattr(state, '_auth_cache', None)
    if not force and cached and cached[0] == key and time.monotonic() - cached[1] < 15:
        return cached[2]
    target = '%s://127.0.0.1:%d' % (state.cfg.get('backend_scheme', 'https'), port)
    ssl_option = None if state.cfg.get('backend_verify_tls') else False
    try:
        import aiohttp
        timeout = aiohttp.ClientTimeout(total=12)
        async with state.http.get(target + '/', ssl=ssl_option, timeout=timeout, auto_decompress=True) as response:
            page = await response.text()
        match = re.search(r'"csrfToken"\s*:\s*"([^"]+)"', page)
        if not match: raise ValueError('未找到原生鉴权令牌')
        headers = {'Content-Type': 'application/json', 'Connect-Protocol-Version': '1',
                   'Origin': target, 'x-codeium-csrf-token': match.group(1)}
        async def rpc(method):
            url = target + '/exa.language_server_pb.LanguageServerService/' + method
            async with state.http.post(url, data=b'{}', headers=headers, ssl=ssl_option,
                                       timeout=timeout, auto_decompress=True) as response:
                if response.status != 200: raise ValueError('原生鉴权接口返回 HTTP %d' % response.status)
                return await response.json()
        token = await rpc('HasAuthToken')
        auth = (await rpc('GetAuthStatus')).get('authResult', {}) if token.get('hasToken') else {}
        authorized = bool(auth.get('hasValidAuth'))
        kind = 'ok' if authorized else ('ineligible' if 'ineligible' in auth else 'login_required')
        message = str(auth.get('uiMessage') or '')
        reason = '本次检测通过：Google 登录和账号权限响应正常' if authorized else '可能是凭据过期、账号权限不足或服务暂时不可用；请重新检测或检查授权'
        if kind == 'ineligible' and 'location' in message.lower():
            reason = '可能是账号地区、出口地区识别或 Google 服务地区限制导致资格检查未通过'
        elif kind == 'ineligible': reason = '可能是账号资格、套餐权限或服务可用性检查未通过'
        elif not token.get('hasToken'):
            reason = '可能是尚未完成登录、授权凭据缺失或登录已失效'
        result = dict(authorized=authorized, token_present=bool(token.get('hasToken')),
                      error_kind=kind, reason=reason[:1000])
    except (aiohttp.ClientError, OSError, ValueError, asyncio.TimeoutError):
        result = dict(authorized=False, token_present=None, error_kind='probe_failed',
                      reason='可能是网络超时、代理连接或引擎接口异常，暂时无法完成登录检测')
    state._auth_cache = (key, time.monotonic(), result)
    return result
