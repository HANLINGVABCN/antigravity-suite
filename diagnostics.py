import asyncio
import time
from types import SimpleNamespace

from auth_probe import auth_status
from process_supervisor import ProcessSupervisor


async def run_detection(state, p_id):
    async with state.lock:
        profile=state.profiles.get_profile(p_id)
        if not profile: return
        proxy=state.proxy_url(profile)
        proxy_result=None
        if proxy:
            proxy_result=await asyncio.to_thread(state.proxies.test_proxy,profile['proxy_id'])
            if not proxy_result.get('ok'):
                state.diagnostics[p_id]=dict(status='abnormal',severity='error',authorized=False,
                    reason='可能是代理无法连接、节点鉴权失败或出口网络不稳定；请检查节点配置',checked_at=time.time())
                return
        temporary=None
        bridge=None
        context=state
        old_home=state.profiles.home
        old_binding=state.profiles.bound_profile
        try:
            if p_id!=state.active or not state.supervisor.active_port:
                if not state.supervisor.installed():
                    state.diagnostics[p_id]=dict(status='abnormal',severity='warning',authorized=False,
                        reason='可能是尚未安装可用的反重力本体，暂时不能完成原生账号检测；凭据仍已保存',checked_at=time.time())
                    return
                native=state.profiles.native_runtime.spec_for(p_id)
                if native:
                    state.profiles.bind_home(p_id)
                    temporary=ProcessSupervisor(state.cfg['antigravity_bin'],state.cfg['display_num'],runtime_home=state.profiles.home)
                    temporary.configure_native(native)
                    # Inspect an existing app without terminating or rewriting its data.
                    already_running=await asyncio.to_thread(temporary.adopt_existing)
                    if not already_running:await asyncio.to_thread(state.profiles.restore_profile,p_id)
                else:
                    await asyncio.to_thread(state.profiles.restore_profile,p_id)
                    temporary=ProcessSupervisor(state.cfg['antigravity_bin'],state.cfg['display_num'],runtime_home=state.profiles.home)
                    already_running=False
                temporary.is_restarting=True
                from outbound_proxy import OutboundProxy
                bridge=await OutboundProxy(proxy).__aenter__()
                await asyncio.to_thread(temporary.start_antigravity,bridge.url)
                context=SimpleNamespace(cfg=state.cfg,supervisor=temporary,http=state.http,active=p_id,busy=False)
            result=await auth_status(context,force=True)
            state.diagnostics[p_id]=dict(result,status='normal' if result['authorized'] else 'abnormal',
                severity='success' if result['authorized'] else 'error',checked_at=time.time(),
                proxy_ip=proxy_result.get('ip') if proxy_result else None)
        except asyncio.CancelledError:
            raise
        except Exception:
            state.diagnostics[p_id]=dict(status='abnormal',severity='error',authorized=False,
                reason='可能是引擎启动失败、凭据异常或网络连接问题；请检查运行日志和代理',checked_at=time.time())
        finally:
            if temporary:
                temporary._running=False
                if already_running:temporary.process=None
                else:
                    await asyncio.to_thread(temporary.close)
                    await asyncio.to_thread(state.profiles.save_active_to_profile,p_id)
            if bridge: await bridge.close()
            state.profiles._set_home(old_home)
            state.profiles.bound_profile=old_binding
