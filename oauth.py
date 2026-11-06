import base64
import asyncio
import datetime
import hashlib
import html
import os
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import aiohttp
from aiohttp import web
from outbound_proxy import OutboundProxy
from config import atomic_json
from oauth_registration import resolve_registration

SCOPES = 'https://www.googleapis.com/auth/cloud-platform https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/userinfo.profile https://www.googleapis.com/auth/cclog https://www.googleapis.com/auth/experimentsandconfigs https://www.googleapis.com/auth/aicode'

def b64(data): return base64.urlsafe_b64encode(data).decode().rstrip('=')

async def google_api(request, s, path, data):
    if path == 'auth/google/callback' and request.method == 'GET':
        value = html.escape(str(request.url), quote=True)
        return web.Response(text='<html lang="zh-CN"><meta charset="utf-8"><title>授权回调</title>'
                           '<p>请将地址栏的完整网址复制回配置大厅。提交并验证后才能确认授权成功。</p>'
                           '<input style="width:95%" readonly value="' + value + '"></html>', content_type='text/html')
    if request.method != 'POST': raise web.HTTPMethodNotAllowed(request.method, ['POST'])
    now = time.time()
    client_id,client_secret=await asyncio.to_thread(resolve_registration,s.cfg)
    s.pending_oauth = {k:v for k,v in s.pending_oauth.items() if now-v['created_at'] < 600}
    if path == 'auth/google/start':
        if len(s.pending_oauth) >= 20: raise ValueError('待完成授权过多，请稍后重试')
        state = secrets.token_urlsafe(24)
        verifier = b64(secrets.token_bytes(32))
        redirect = s.cfg.get('oauth_redirect_uri') or 'http://localhost:%d/__suite/api/auth/google/callback' % s.cfg['listen_port']
        s.pending_oauth[state] = dict(verifier=verifier, redirect_uri=redirect, created_at=now,
                                     session=request.cookies.get('ag_session'))
        params = dict(client_id=client_id, redirect_uri=redirect,
                      response_type='code', access_type='offline', prompt='consent', scope=SCOPES,
                      code_challenge=b64(hashlib.sha256(verifier.encode()).digest()), code_challenge_method='S256', state=state)
        return web.json_response(dict(ok=True, state=state, redirect_uri=redirect,
                                     auth_url='https://accounts.google.com/o/oauth2/v2/auth?' + urlencode(params)))
    if path != 'auth/google/complete': raise web.HTTPNotFound()
    code = str(data.get('code', '')).strip()
    state = str(data.get('state', '')).strip()
    if '://' in code:
        query = parse_qs(urlsplit(code).query)
        if query.get('error'): raise ValueError('Google 授权被拒绝：' + query['error'][0])
        pasted_state = query.get('state', [''])[0]
        if pasted_state != state: raise ValueError('回调网址与当前授权会话不匹配')
        code = query.get('code', [''])[0]
    info = s.pending_oauth.get(state)
    if not code or not info or info['session'] != request.cookies.get('ag_session'):
        raise ValueError('授权会话已失效或不匹配，请重新获取授权链接')
    proxy_id = data.get('proxy_id', '')
    px = s.proxies.get_proxy(proxy_id) if proxy_id else None
    if proxy_id and not px: raise ValueError('所选代理不存在')
    params = dict(client_id=client_id,
                  code=code, code_verifier=info['verifier'], grant_type='authorization_code', redirect_uri=info['redirect_uri'])
    if client_secret:params['client_secret']=client_secret
    s.pending_oauth.pop(state, None)
    async with OutboundProxy(s.proxies.get_proxy_url(px) if px else None) as bridge, aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30), trust_env=False) as client:
        async with client.post('https://oauth2.googleapis.com/token', data=params,proxy=bridge.url) as response:
            token = await response.json()
            if response.status != 200 or not token.get('access_token'):
                raise ValueError('Google 授权码交换失败：' + str(token.get('error', response.status)))
        if not token.get('refresh_token'): raise ValueError('未获得可续期凭据，请重新授权并确认同意')
        async with client.get('https://www.googleapis.com/oauth2/v2/userinfo',
                              headers={'Authorization':'Bearer ' + token['access_token']},proxy=bridge.url) as response:
            if response.status != 200: raise ValueError('Google 用户信息验证失败')
            user = await response.json()
    expiry = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=int(token.get('expires_in',3599)))
    content = dict(token=dict(access_token=token['access_token'], token_type=token.get('token_type','Bearer'),
                             refresh_token=token['refresh_token'], expiry=expiry.isoformat().replace('+00:00','Z'),
                             expires_in=token.get('expires_in',3599)), auth_method='consumer',
                   id_token_email=user.get('email',''), writer_id='antigravity')
    async with s.lock:
        p = s.profiles.create_profile(data.get('name','Google账号'), proxy_id, user.get('email',''))
        atomic_json(s.profiles.profile_dir(p['id'])/'store/jetski-standalone-oauth-token', content)
    s.request_detection(p['id'])
    return web.json_response(dict(ok=True, email=user.get('email',''), profile=p))
