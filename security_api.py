import asyncio
import copy
import io
import secrets
import time

from aiohttp import web
from config import atomic_json,check_password
from two_factor import new_secret,new_recovery_codes,matching_counter,check_factor,provisioning_uri


def commit_security(state, updates, keep_transport=None):
    candidate=copy.deepcopy(state.cfg)
    candidate.update(updates)
    rotate=candidate.get('session_secret')!=state.cfg.get('session_secret')
    atomic_json(state.data/'config.json',candidate)
    state.cfg.clear();state.cfg.update(candidate)
    if rotate:
        for transport in list(state.protected_connections):
            if transport is not keep_transport:transport.close()


def current_attempts(state, request):
    key=request.remote or 'unknown'
    now=time.monotonic()
    state.security_attempts={ip:[t for t in tries if now-t<60] for ip,tries in state.security_attempts.items() if any(now-t<60 for t in tries)}
    attempts=state.security_attempts.setdefault(key,[])
    if len(attempts)>=5:raise web.HTTPTooManyRequests(text='验证尝试过多，请一分钟后重试')
    return attempts


async def verify_current(state, request, data, require_factor=None):
    attempts=current_attempts(state,request)
    password_ok=await asyncio.to_thread(check_password,data.get('current_password',''),state.cfg['auth_password_hash'])
    if not password_ok:
        attempts.append(time.monotonic());raise ValueError('当前管理员密码不正确')
    needed=state.cfg.get('mfa_enabled',False) if require_factor is None else require_factor
    updates={}
    if needed:
        updates=check_factor(state.cfg,data.get('code',''))
        if updates is None:
            attempts.append(time.monotonic());raise ValueError('验证码或恢复码不正确，可能已使用或设备时间不一致')
    return updates


def prune_setups(state):
    now=time.time()
    state.pending_mfa={token:item for token,item in state.pending_mfa.items() if item['expires_at']>now}


async def security_api(request,state,path,data):
    from server import session_token,secure_cookie
    def result(**values):return web.json_response(dict(ok=True,**values))
    def renew(response):
        response.set_cookie('ag_session',session_token(state.cfg),httponly=True,samesite='Lax',secure=secure_cookie(request,state.cfg),max_age=30*86400,path='/')
        return response
    async with state.auth_lock:
        prune_setups(state)
        if path=='system/security/status' and request.method=='GET':
            return result(enabled=bool(state.cfg.get('mfa_enabled')),recovery_codes_remaining=len(state.cfg.get('mfa_recovery_hashes',[])))
        if path=='system/security/qr' and request.method=='GET':
            item=state.pending_mfa.get(request.query.get('setup_token'))
            if not item or item['session']!=request.cookies.get('ag_session'):raise ValueError('绑定已过期，请重新生成二维码')
            # Generate locally: neither the URI nor secret is sent to a QR service.
            import qrcode
            from qrcode.image.svg import SvgPathImage
            def render():
                buffer=io.BytesIO();qrcode.make(item['uri'],image_factory=SvgPathImage,box_size=8,border=4).save(buffer)
                return buffer.getvalue()
            return web.Response(body=await asyncio.to_thread(render),content_type='image/svg+xml')
        if request.method!='POST':raise web.HTTPMethodNotAllowed(request.method,['GET','POST'])
        if path=='system/security/setup':
            if state.cfg.get('mfa_enabled'):raise ValueError('验证器已开启；如需更换，请先用当前验证码关闭')
            await verify_current(state,request,data,require_factor=False)
            session=request.cookies.get('ag_session')
            state.pending_mfa={token:item for token,item in state.pending_mfa.items() if item['session']!=session}
            if len(state.pending_mfa)>=8:raise ValueError('待完成绑定过多，请稍后重试')
            secret=new_secret();token=secrets.token_urlsafe(32)
            uri=provisioning_uri(secret,state.cfg['auth_user'])
            state.pending_mfa[token]=dict(secret=secret,uri=uri,session=session,expires_at=time.time()+600)
            return result(setup_token=token,secret=secret,otpauth_uri=uri,expires_in=600)
        if path=='system/security/cancel':
            token=data.get('setup_token');item=state.pending_mfa.get(token)
            if item and item['session']==request.cookies.get('ag_session'):state.pending_mfa.pop(token,None)
            return result()
        if path=='system/security/enable':
            if state.cfg.get('mfa_enabled'):raise ValueError('验证器已经开启')
            await verify_current(state,request,data,require_factor=False)
            item=state.pending_mfa.get(data.get('setup_token'))
            if not item or item['session']!=request.cookies.get('ag_session'):raise ValueError('绑定已过期，请重新生成二维码')
            counter=matching_counter(item['secret'],data.get('code',''))
            if counter is None:
                current_attempts(state,request).append(time.monotonic())
                raise ValueError('验证码不正确，请检查验证器和手机时间后重试')
            codes,hashes=new_recovery_codes()
            commit_security(state,dict(mfa_enabled=True,mfa_secret=item['secret'],mfa_last_counter=counter,
                mfa_recovery_hashes=hashes,session_secret=secrets.token_hex(32)),keep_transport=request.transport)
            state.pending_mfa.clear()
            return renew(result(enabled=True,recovery_codes=codes,message='验证器已开启；其他登录会话已失效'))
        if path in ('system/security/disable','system/security/recovery'):
            if not state.cfg.get('mfa_enabled'):raise ValueError('验证器尚未开启')
            updates=await verify_current(state,request,data)
            updates['session_secret']=secrets.token_hex(32)
            if path=='system/security/disable':
                updates.update(mfa_enabled=False,mfa_secret='',mfa_last_counter=-1,mfa_recovery_hashes=[])
                commit_security(state,updates,keep_transport=request.transport);state.pending_mfa.clear()
                return renew(result(enabled=False,message='验证器已关闭；旧恢复码和其他会话已失效'))
            codes,hashes=new_recovery_codes();updates['mfa_recovery_hashes']=hashes
            commit_security(state,updates,keep_transport=request.transport)
            return renew(result(enabled=True,recovery_codes=codes,message='恢复码已重新生成；旧恢复码和其他会话已失效'))
    raise web.HTTPNotFound()
