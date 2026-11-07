import asyncio
import re
import time

import aiohttp

STAGES={'page':'读取引擎首页','HasAuthToken':'读取当前登录凭据','GetAuthStatus':'等待 Google 账号权限响应'}


async def auth_status(state, force=False, progress=None):
    port = state.supervisor.active_port
    if state.busy or not port:
        return dict(authorized=False, token_present=False, error_kind='not_ready', reason='请先启动一个档案')
    key = (state.active, port)
    cached = getattr(state, '_auth_cache', None)
    if not force and cached and cached[0] == key and time.monotonic() - cached[1] < 15:
        return cached[2]
    started=time.monotonic();stage='page';present=None;http_status=None;result=None
    timeout_value=max(.05,min(60,float(state.cfg.get('auth_probe_timeout',30))))
    ssl_option=None if state.cfg.get('backend_verify_tls') else False
    for attempt in range(1,3):
        try:
            stage='page';http_status=None
            if attempt>1 and hasattr(state.supervisor,'detect_port_from_log'):
                port=await asyncio.to_thread(state.supervisor.detect_port_from_log) or port
            target='%s://127.0.0.1:%d'%(state.cfg.get('backend_scheme','https'),port)
            def report(name):
                nonlocal stage,http_status
                stage=name;http_status=None
                if progress:progress(dict(stage=name,attempt=attempt,elapsed_seconds=int(time.monotonic()-started),reason=STAGES[name]+('（正在重试）' if attempt>1 else '')))
            report('page')
            timeout=aiohttp.ClientTimeout(total=min(10,timeout_value),sock_connect=min(5,timeout_value))
            async with state.http.get(target+'/',ssl=ssl_option,timeout=timeout,auto_decompress=True) as response:
                http_status=response.status
                if response.status!=200:raise ValueError('HTTP')
                page=await response.text()
            match=re.search(r'"csrfToken"\s*:\s*"([^"]+)"',page)
            if not match:raise ValueError('CSRF')
            headers={'Content-Type':'application/json','Connect-Protocol-Version':'1','Origin':target,'x-codeium-csrf-token':match.group(1)}
            async def rpc(method):
                nonlocal http_status
                report(method)
                timeout=aiohttp.ClientTimeout(total=timeout_value if method=='GetAuthStatus' else min(10,timeout_value),sock_connect=min(5,timeout_value))
                async with state.http.post(target+'/exa.language_server_pb.LanguageServerService/'+method,data=b'{}',headers=headers,ssl=ssl_option,timeout=timeout,auto_decompress=True) as response:
                    http_status=response.status
                    if response.status!=200:raise ValueError('HTTP')
                    return await response.json()
            token=await rpc('HasAuthToken');present=bool(token.get('hasToken'))
            auth=(await rpc('GetAuthStatus')).get('authResult',{}) if present else {}
            authorized=bool(auth.get('hasValidAuth'))
            kind='ok' if authorized else 'ineligible' if 'ineligible' in auth else 'login_required'
            message=str(auth.get('uiMessage') or '')
            reason='本次检测通过：Google 登录和账号权限响应正常' if authorized else '可能是凭据过期、账号权限不足或服务暂时不可用；请重新检测或检查授权'
            if kind=='ineligible' and any(word in message.lower() for word in ('location','region','country','地区')):
                reason='可能是账号地区、出口地区识别或 Google 服务地区限制导致资格检查未通过'
            elif kind=='ineligible':reason='可能是账号资格、套餐权限或服务可用性检查未通过'
            elif not present:reason='可能是尚未完成登录、授权凭据缺失或登录已失效'
            result=dict(authorized=authorized,token_present=present,error_kind=kind,reason=reason[:1000]);break
        except asyncio.TimeoutError:
            result=dict(authorized=False,token_present=present,error_kind='probe_timeout',reason='可能是引擎刚启动、服务器资源紧张或网络响应较慢；'+STAGES[stage]+'超时，暂未确认账号是否可用')
        except (aiohttp.ClientError,OSError):
            result=dict(authorized=False,token_present=present,error_kind='engine_connection',reason='可能是引擎启动后端口变化或连接暂时中断；'+STAGES[stage]+'失败，暂未确认账号是否可用')
        except (ValueError,TypeError,AttributeError):
            code='（HTTP '+str(http_status)+'）' if http_status and http_status!=200 else ''
            result=dict(authorized=False,token_present=present,error_kind='engine_response',reason='可能是原生接口尚未就绪或返回格式不兼容；'+STAGES[stage]+'失败'+code+'，暂未确认账号是否可用')
            if http_status not in (502,503,504):break
        if attempt==1:
            if progress:progress(dict(stage=stage,attempt=2,elapsed_seconds=int(time.monotonic()-started),reason='接口暂未响应，等待后自动重试；配置已经保存'))
            await asyncio.sleep(1)
    result.update(probe_stage=stage,probe_attempts=attempt,duration_ms=int((time.monotonic()-started)*1000))
    if http_status and http_status!=200:result['probe_http_status']=http_status
    state._auth_cache=((state.active,port),time.monotonic(),result)
    return result
