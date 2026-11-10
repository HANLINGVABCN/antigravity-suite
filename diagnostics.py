import asyncio
import time
import tempfile
from pathlib import Path
from account_storage import archive_bytes,working_file,write_bytes
from types import SimpleNamespace

from auth_probe import auth_status
from process_supervisor import ProcessSupervisor


async def run_detection(state, p_id):
    started=time.time()
    def progress(value):
        state.diagnostics[p_id]=dict(status='checking',started_at=started,checked_at=None,**value)
    progress(dict(stage='queue',reason='等待检测；配置已经保存'))
    async with state.lock:
        profile=state.profiles.get_profile(p_id)
        if not profile: return
        proxy=state.proxy_url(profile)
        proxy_result=None
        if proxy:
            progress(dict(stage='proxy',reason='正在检查代理节点；配置已经保存'))
            proxy_result=await asyncio.to_thread(state.proxies.test_proxy,profile['proxy_id'])
            if not proxy_result.get('ok'):
                state.diagnostics[p_id]=dict(status='abnormal',severity='error',authorized=False,
                    reason='可能是代理无法连接、节点鉴权失败或出口网络不稳定；请检查节点配置',checked_at=time.time())
                return
        temporary=None
        bridge=None
        context=state
        isolated=None
        try:
            if p_id!=state.active or not state.supervisor.active_port:
                progress(dict(stage='starting',reason='正在准备配置并启动检测引擎；配置已经保存'))
                if not state.supervisor.installed():
                    state.diagnostics[p_id]=dict(status='abnormal',severity='warning',authorized=False,
                        reason='可能是尚未安装可用的反重力本体，暂时不能完成原生账号检测；凭据仍已保存',checked_at=time.time())
                    return
                isolated=tempfile.TemporaryDirectory(dir=state.data,prefix='.account-check-')
                home=Path(isolated.name)
                write_bytes(working_file(home),archive_bytes(state.profiles,p_id))
                temporary=ProcessSupervisor(state.cfg['antigravity_bin'],state.cfg['display_num'],runtime_home=home)
                already_running=False
                temporary.is_restarting=True
                from outbound_proxy import OutboundProxy
                bridge=await OutboundProxy(proxy).__aenter__()
                await asyncio.to_thread(temporary.start_antigravity,bridge.url)
                context=SimpleNamespace(cfg=state.cfg,supervisor=temporary,http=state.http,active=p_id,busy=False)
            result=await auth_status(context,force=True,progress=progress)
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
                    metadata=state.profiles.installed_token(working_file(isolated.name)) if isolated else None
                    if metadata and state.profiles.account_matches(p_id,metadata):
                        write_bytes(state.profiles.profile_dir(p_id)/'store/jetski-standalone-oauth-token',working_file(isolated.name).read_bytes())
            if bridge: await bridge.close()
            if isolated:isolated.cleanup()
