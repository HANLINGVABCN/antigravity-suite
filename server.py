import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web
from config import DATA_DIR, SUITE_DIR, atomic_json, check_password, hash_password, load_config, save_config
from core_installer import CoreInstaller
from gateway import proxy
from process_supervisor import ProcessSupervisor
from profile_manager import ProfileManager
from proxy_manager import ProxyManager
from privacy import privacy_guard

class SuiteState:
    def __init__(self,cfg,profile_mgr=None,supervisor=None):
        self.cfg=cfg; self.static=SUITE_DIR/'static'; self.data=Path(cfg.get('data_dir',DATA_DIR))
        self.cfg['data_dir']=str(self.data)
        self.cfg.setdefault('core_directory',str(self.data/'core'))
        self.profiles=profile_mgr or ProfileManager(self.data,cfg['profiles_dir'],cfg['runtime_home'])
        self.proxies=ProxyManager(self.data)
        self.supervisor=supervisor or ProcessSupervisor(cfg['antigravity_bin'],cfg['display_num'],runtime_home=cfg['runtime_home'])
        self.installer=CoreInstaller(cfg,self.supervisor)
        self.active=cfg.get('active_profile_id'); self.loaded=None; self.busy=False; self.last_error=''
        self.lock=asyncio.Lock(); self.pending_oauth={}; self.login_attempts={}; self.http=None
        self.launch_task=None; self.starting_profile_id=None; self.diagnostics={}; self.detection_tasks={}
        self.outbound_bridge=None; self.engine_proxy_url=None
        self.last_switch=0
        self.pending_imports={}
        self.auth_lock=asyncio.Lock();self.pending_mfa={};self.security_attempts={}
        self.protected_connections=set()
        self.discovery_enabled=cfg.get('discover_existing',supervisor is None)
        self.discovered=dict(binary=None,installations=[],configurations=[])
        self.discovery_task=None;self.discovery_running=False
        self.initial_import_pending=False;self.native_requires_restart=False;self.native_runtime_proxy_id=None
        from login_sessions import LoginSessions
        self.sessions=LoginSessions(self.data)
        self.exit_task=None;self.exit_state=dict(phase='idle',message='')
        self.opening_login=False
        self.login_environment_open=False
        self._runtime_running=None;self._runtime_identity=None;self._runtime_exit_sequence=0
        from native_control import NativeControl
        from session_operations import SessionOperations
        self.native_controls=NativeControl(self);self.session_ops=SessionOperations(self)
        from suite_updater import SuiteUpdater
        self.updater=SuiteUpdater(self)
        from service_restart import ServiceRestart
        self.service_restart=ServiceRestart(self)
    async def refresh_existing(self, import_logins=False):
        if not self.discovery_enabled:return
        from discovery import discover
        async with self.lock:
            found=await asyncio.to_thread(discover,self.cfg)
            self.discovered=found
            if found['binary']:
                changed=self.cfg.get('installation_directory')!=str(Path(found['binary']).parent) or self.cfg.get('antigravity_bin')!=found['binary']
                self.cfg['installation_directory']=str(Path(found['binary']).parent)
                self.cfg['antigravity_bin']=found['binary'];self.supervisor.antigravity_bin=found['binary']
                if changed:self.persist()
            self.profiles.native_runtime.refresh_installations(found['configurations'])
            for spec in self.profiles.native_runtime.installations:
                if spec.get('available'):
                    self.native_controls.publish(spec)
                    try:await asyncio.to_thread(self.profiles.cli_credentials.ensure,spec)
                    except (OSError,ValueError) as error:self.last_error=str(error)
            if import_logins:self.initial_import_pending=True
            if self.initial_import_pending and not self.updater.busy():
                imported=await asyncio.to_thread(self.profiles.import_installed_accounts_once,self.cfg.get('machine_name'))
                self.initial_import_pending=False
                if not self.active and imported:
                    self.active=imported[0];self.persist()
                for identifier in imported:
                    if identifier!=self.cfg.get('default_profile_id'):self.request_detection(identifier)
            await self.observe_runtime()
            await self.sync_native_login()

    async def observe_runtime(self):
        if self.busy or self.starting_profile_id or self.opening_login or self.updater.busy():return
        if self.installer.task and not self.installer.task.done():return
        if hasattr(self.supervisor,'refresh_runtime'):
            if self.profiles.get_profile(self.active):self.bind_engine_home(self.active)
            else:
                spec=self.session_ops.runtime()
                if spec and hasattr(self.supervisor,'configure_native'):self.supervisor.configure_native(spec)
            await asyncio.to_thread(self.supervisor.refresh_runtime)
        process=self.supervisor.process
        running=bool(process and process.poll() is None)
        identity=(getattr(process,'pid',id(process)),getattr(process,'identity',None)) if running else None
        exits=getattr(self.supervisor,'exit_sequence',0)
        changed=False
        if running:
            if self.cfg.get('engine_stopped'):
                self.cfg['engine_stopped']=False;changed=True
            if identity!=self._runtime_identity:
                self._auth_cache=None
                profile=self.profiles.get_profile(self.active)
                if hasattr(self.supervisor,'current_proxy_url'):
                    observed_proxy=self.supervisor.current_proxy_url
                    self.native_runtime_proxy_id=(profile.get('proxy_id') or None) if profile and observed_proxy and observed_proxy==self.engine_proxy_url else None
                self.native_requires_restart=bool(profile and (profile.get('proxy_id') or None)!=self.native_runtime_proxy_id)
            if self.active:self.loaded=self.active
            if self.cfg.get('native_logged_out'):self.login_environment_open=True
        elif self._runtime_running is True or exits>self._runtime_exit_sequence:
            self.cfg['engine_stopped']=True;self.login_environment_open=False;self._auth_cache=None
            self.native_requires_restart=False;changed=True
        self._runtime_running=running;self._runtime_identity=identity;self._runtime_exit_sequence=exits
        if changed:self.persist()

    async def refresh_runtime_status(self):
        if self.lock.locked():return
        async with self.lock:await self.observe_runtime()

    async def initialize_account_layout(self):
        spec=self.session_ops.runtime(create=True)
        if not self.profiles.native_runtime.selected:self.profiles.native_runtime.selected=spec
        marker=self.data/'account-layout-v2.json'
        pending_recovery=any((p/'journal.json').is_file() for p in self.profiles.history.root.parent.glob('.history-transfer-*'))
        if marker.is_file() and not pending_recovery:
            self.profiles.history.use_native(Path(spec['native_home'])/'.gemini/antigravity');return
        async with self.lock:
            running=bool(self.supervisor.process and self.supervisor.process.poll() is None)
            self.busy=True;self.supervisor.is_restarting=True
            try:
                await asyncio.to_thread(self.profiles.cli_credentials.stop,spec)
                await asyncio.to_thread(self.supervisor.stop_antigravity)
                await asyncio.to_thread(self.profiles.migrate_account_layout_once,spec)
                if running and self.profiles.get_profile(self.active):
                    self.bind_engine_home(self.active)
                    await self.prepare_engine_proxy(self.profiles.get_profile(self.active))
                    await asyncio.to_thread(self.supervisor.start_antigravity,self.engine_proxy_url)
            finally:self.busy=False;self.supervisor.is_restarting=False

    async def import_account(self,path,include_proxy=False,include_history=False,history_mode='merge'):
        await self.ensure_switch_allowed()
        if self.sessions.in_progress():raise ValueError('请先完成或取消当前登录，再导入')
        was_running=bool(self.supervisor.process and self.supervisor.process.poll() is None)
        if include_history:
            spec=self.session_ops.runtime()
            if spec:await asyncio.to_thread(self.profiles.cli_credentials.stop,spec)
            await self.stop_and_save()
            self.busy=True;self.supervisor.is_restarting=True
        try:
            return await asyncio.to_thread(self.profiles.import_profile,path,self.proxies,include_proxy,include_history,history_mode)
        finally:
            if include_history:
                try:
                    if was_running:await asyncio.to_thread(self.supervisor.start_antigravity,self.engine_proxy_url)
                finally:self.busy=False;self.supervisor.is_restarting=False

    async def sync_native_login(self, force=False):
        if not self.discovery_enabled or self.updater.busy():return
        observed=self.profiles.native_runtime.observe_login(force,self.sessions.state.get('working'))
        if not observed:return
        native=observed['native'];token=observed['token']
        if not token:
            if self.active is not None or not self.cfg.get('native_logged_out'):
                self.active=None;self.loaded=None;self.native_requires_restart=False
                self.profiles.native_runtime.detach()
                self.sessions.exited(native)
                self.cfg['native_logged_out']=True;self.persist()
            return
        active=self.profiles.get_profile(self.active)
        matches=bool(active and self.profiles.account_matches(active['id'],token))
        pending=self.sessions.state.get('pending')
        if pending and not pending.get('confirmed') and time.time()-pending['started_at']<=1800:return
        event=self.sessions.new_login(token,self.active,matches)
        profile=next((p for p in self.profiles.profiles if p.get('login_event_id')==event),None) if event else active
        created=False
        if not profile:
            number=1+sum(p.get('email')==token['email'] for p in self.profiles.list_profiles())
            name=(token['email'] or '账号')+' · 登录 '+str(number)
            profile=self.profiles.create_profile(name,self.native_runtime_proxy_id or '',token['email'],identifier=event)
            profile['login_event_id']=event
            profile['pending_native_import']=True;self.profiles._save()
            created=True
        needs_snapshot=created
        if needs_snapshot:
            try:await asyncio.to_thread(self.profiles.capture_installed_account,profile,native,token)
            except Exception:
                if created:self.profiles.delete_profile(profile['id'])
                raise
        if created:profile.pop('pending_native_import',None);self.profiles._save()
        if not created and self.sessions.state.get('full')!=token.get('full_fingerprint'):
            await asyncio.to_thread(self.profiles.renew_session_token,profile['id'],native,token)
        if event:self.sessions.captured(event,profile['id'],token)
        elif self.sessions.state.get('profile_id')!=profile['id'] or self.sessions.state.get('full')!=token.get('full_fingerprint'):
            self.sessions.renewed(profile['id'],token)
        if self.sessions.state.get('working')!=native:self.sessions.state['working']=native;self.sessions.save()
        common=self.profiles.native_runtime.binding
        changed=self.active!=profile['id'] or not common or common['profile_id']!=profile['id'] or self.cfg.get('native_logged_out')
        email_changed=bool(token['email'] and profile.get('email')!=token['email'])
        if not changed and not email_changed and not needs_snapshot:
            running=bool(self.supervisor.process and self.supervisor.process.poll() is None)
            self.native_requires_restart=running and (profile.get('proxy_id') or None)!=self.native_runtime_proxy_id
            return
        if email_changed:self.profiles.update_profile(profile['id'],email=token['email'])
        running=bool(self.supervisor.process and self.supervisor.process.poll() is None)
        self.profiles.native_runtime.associate(profile['id'],native)
        self.bind_engine_home(profile['id'])
        self.active=profile['id'];self.loaded=profile['id'];self.cfg['native_logged_out']=False
        self.login_environment_open=False;self.cfg['login_environment']=False
        if not running and hasattr(self.supervisor,'adopt_existing'):
            running=await asyncio.to_thread(self.supervisor.adopt_existing)
            if running:self.native_runtime_proxy_id=None
        self.native_requires_restart=running and (profile.get('proxy_id') or None)!=self.native_runtime_proxy_id
        self.persist()
        if running and not self.native_requires_restart and not self.busy:self.request_detection(profile['id'])
    def prune_imports(self):
        now=time.time()
        for token,item in list(self.pending_imports.items()):
            if item['expires_at']<now:
                Path(item['path']).unlink(missing_ok=True)
                self.pending_imports.pop(token,None)
    async def prepare_engine_proxy(self, profile):
        from outbound_proxy import OutboundProxy
        upstream=self.proxy_url(profile)
        if self.outbound_bridge and getattr(self.outbound_bridge,'upstream',object())==upstream and (not upstream or getattr(self.outbound_bridge,'server',None)):
            self.publish_cli_proxy(self.session_ops.runtime(),profile,self.engine_proxy_url)
            return self.engine_proxy_url
        if self.outbound_bridge: await self.outbound_bridge.close()
        self.outbound_bridge=OutboundProxy(upstream)
        self.outbound_bridge.port=int(self.cfg.get('shared_proxy_port',0))
        await self.outbound_bridge.__aenter__()
        self.engine_proxy_url=self.outbound_bridge.url
        if self.engine_proxy_url and not self.cfg.get('shared_proxy_port'):
            self.cfg['shared_proxy_port']=urlsplit(self.engine_proxy_url).port;self.persist()
        self.publish_cli_proxy(self.session_ops.runtime(),profile,self.engine_proxy_url)
        return self.engine_proxy_url
    def publish_cli_proxy(self,native,profile=None,proxy_url=None,required=None):
        if not native or not self.profiles.cli_proxy:return
        if required is None:required=bool(profile and profile.get('proxy_id'))
        self.profiles.cli_proxy.publish(native,proxy_url,required,ready=not required or bool(proxy_url),profile_id=profile['id'] if profile else None)
    async def ensure_switch_allowed(self):
        if self.service_restart.busy():raise ValueError('后台正在重启，请等待恢复')
        if self.launch_task and not self.launch_task.done():raise ValueError('正在启动档案，请等待完成')
        if self.exit_task and not self.exit_task.done():raise ValueError('正在退出配置，请稍后重试')
    def bind_engine_home(self, p_id):
        self.profiles.bind_home(p_id)
        p=self.profiles.get_profile(p_id)
        native=self.profiles.native_runtime.spec_for(p_id)
        if native and hasattr(self.supervisor,'configure_native'):self.supervisor.configure_native(native)
        elif hasattr(self.supervisor,'set_home'):
            self.supervisor.set_home(self.profiles.home);self.supervisor.display=self.cfg['display_num']
        if native:self.native_controls.publish(native)
    def persist(self):
        self.cfg['active_profile_id']=self.active
        atomic_json(self.data/'config.json',self.cfg)
    def proxy_url(self,p):
        if not p or not p.get('proxy_id'): return None
        item=self.proxies.get_proxy(p['proxy_id'])
        if not item: raise ValueError('绑定的代理不存在，请重新选择')
        return self.proxies.get_proxy_url(item)
    def request_start(self, target, restart=False):
        if self.sessions.in_progress():raise ValueError('登录尚未结束，请完成或取消后再切换配置')
        target=self.profiles.native_runtime.resolve(target)
        if not self.profiles.get_profile(target): raise ValueError('档案不存在')
        if target==self.active and self.supervisor.active_port and not self.busy and not restart and not self.native_requires_restart: return
        if self.launch_task and not self.launch_task.done():
            if self.starting_profile_id==target: return
            raise ValueError('已有档案正在启动，请等待完成')
        if self.exit_task and not self.exit_task.done():raise ValueError('正在退出配置，请稍后重试')
        if time.monotonic()-self.last_switch<4: raise ValueError('切换过于频繁，请等待几秒后重试')
        self.last_switch=time.monotonic()
        self.starting_profile_id=target
        async def launch():
            try:
                await self.activate(target)
                self.request_detection(target)
            except Exception as e:
                self.last_error=str(e)
                self.diagnostics[target]=dict(status='abnormal',severity='error',reason='可能是启动环境、运行文件或代理连接存在问题：'+str(e),checked_at=time.time())
            finally: self.starting_profile_id=None
        self.launch_task=asyncio.create_task(launch())
    def request_detection(self, target):
        target=self.profiles.native_runtime.resolve(target)
        if not self.profiles.get_profile(target): raise ValueError('档案不存在')
        task=self.detection_tasks.get(target)
        if task and not task.done(): return
        self.diagnostics[target]=dict(status='checking',reason='正在检查登录、代理和引擎连接',checked_at=None)
        async def detect():
            from diagnostics import run_detection
            try: await run_detection(self,target)
            except Exception:
                self.diagnostics[target]=dict(status='abnormal',severity='error',reason='可能是检测进程、网络连接或凭据读取异常，请查看服务日志',checked_at=time.time())
        self.detection_tasks[target]=asyncio.create_task(detect())
    def request_open_login(self):
        if self.login_environment_open and self.supervisor.active_port and not self.busy:return
        if self.launch_task and not self.launch_task.done():return
        if self.exit_task and not self.exit_task.done():raise ValueError('正在退出配置，请稍后重试')
        if not self.sessions.state.get('waiting') and self.active is not None and not self.cfg.get('native_logged_out'):
            raise ValueError('请先退出当前配置，再打开登录界面')
        if self.installer.task and not self.installer.task.done():raise ValueError('本体操作尚未完成')
        self.opening_login=True;self.last_error=''
        async def launch():
            try:
                if not self.sessions.state.get('waiting'):
                    # A deleted/detached current card can leave a credential-generation record behind.
                    # Use the same backed-up local exit to clear only the working login.
                    await self.session_ops.exit()
                else:
                    async with self.lock:
                        self.busy=True;self.supervisor.is_restarting=True
                        await asyncio.to_thread(self.supervisor.stop_antigravity)
                        await self.session_ops.start_empty(self.session_ops.runtime(create=True))
                        self.cfg['login_environment']=True;self.persist()
            except Exception as error:self.last_error=str(error)
            finally:self.busy=False;self.supervisor.is_restarting=False;self.opening_login=False
        self.launch_task=asyncio.create_task(launch())
    async def activate(self,target):
        p=self.profiles.get_profile(target)
        if not p: raise ValueError('档案不存在')
        if self.installer.task and not self.installer.task.done(): raise ValueError('本体操作中，请等待完成')
        async with self.lock:
            await self.sync_native_login(force=True)
            old=self.active; old_profile=self.profiles.get_profile(old); old_loaded=self.loaded
            if old!=target or (self.native_runtime_proxy_id or None)!=(p.get('proxy_id') or None):
                native=self.session_ops.runtime()
                if native:await asyncio.to_thread(self.profiles.cli_credentials.stop,native)
            old_running=bool(self.supervisor.process and self.supervisor.process.poll() is None)
            proxy_url=self.proxy_url(p); self.busy=True; self.supervisor.is_restarting=True
            switched=False
            try:
                if p.get('kind')=='existing' and not p.get('available'):raise ValueError('已有共享配置不可接管，请检查本体和权限')
                if p.get('proxy_id'):
                    result=await asyncio.to_thread(self.proxies.test_proxy,p['proxy_id'])
                    if not result.get('ok'): raise ValueError('可能是代理不可用或鉴权失败，已阻止启动，避免改走直连')
                await asyncio.to_thread(self.supervisor.stop_antigravity)
                if old_profile and old_loaded==old: await asyncio.to_thread(self.profiles.save_active_to_profile,old)
                self.bind_engine_home(target)
                # Stop any exact-matching orphan before changing runtime or proxy parameters.
                await asyncio.to_thread(self.supervisor.stop_antigravity)
                if not self.profiles.native_runtime.selected:
                    self.profiles.native_runtime.selected=self.session_ops.runtime(create=True)
                if self.cfg.get('shared_native_runtime',True):
                    if not self.profiles.native_runtime.selected and self.sessions.state.get('working'):
                        self.profiles.native_runtime.selected=self.sessions.state['working']
                    native=self.profiles.native_runtime.selected
                    if native and not self.profiles.native_runtime.spec_for(target):
                        original=ProcessSupervisor(self.cfg['antigravity_bin'],native['native_display'],runtime_home=native['native_home'])
                        original.configure_native(native)
                        await asyncio.to_thread(original.stop_antigravity)
                    await asyncio.to_thread(self.profiles.native_runtime.activate,target)
                    switched=True
                await asyncio.to_thread(self.profiles.restore_profile,target)
                self.bind_engine_home(target)
                switched=True
                self.loaded=target
                current_native=self.profiles.native_runtime.spec_for(target)
                token=self.profiles.installed_token(self.profiles.home/'.gemini/jetski-standalone-oauth-token')
                self.sessions.restored(target,token)
                self.sessions.state['working']=current_native;self.sessions.save()
                await self.session_ops.ensure_desktop_guard()
                proxy_url=await self.prepare_engine_proxy(p)
                await asyncio.to_thread(self.supervisor.start_antigravity,proxy_url)
                self.native_runtime_proxy_id=p.get('proxy_id') or None;self.native_requires_restart=False
                self.cfg['native_logged_out']=False
                self.cfg['login_environment']=False;self.login_environment_open=False
                self.active=target; self.last_error='';self.cfg['engine_stopped']=False; self.persist()
            except Exception as e:
                self.last_error=str(e)
                await asyncio.to_thread(self.supervisor.stop_antigravity)
                if old_profile:
                    try:
                        if switched:
                            if self.cfg.get('shared_native_runtime',True):
                                await asyncio.to_thread(self.profiles.native_runtime.activate,old)
                            await asyncio.to_thread(self.profiles.restore_profile,old)
                            self.bind_engine_home(old)
                        if old_running:
                            await self.prepare_engine_proxy(old_profile)
                            await asyncio.to_thread(self.supervisor.start_antigravity,self.engine_proxy_url)
                            self.native_runtime_proxy_id=old_profile.get('proxy_id') or None
                            self.native_requires_restart=False
                            token=self.profiles.installed_token(self.profiles.home/'.gemini/jetski-standalone-oauth-token')
                            self.sessions.restored(old,token)
                    except Exception: self.last_error+='；原档案恢复启动失败，请查看日志'
                self.active=old; self.loaded=old_loaded; self.persist()
                raise
            finally:
                self.busy=False; self.supervisor.is_restarting=False
    async def stop_and_save(self):
        await self.sync_native_login(force=True)
        self.busy=True; self.supervisor.is_restarting=True
        try:
            await asyncio.to_thread(self.supervisor.stop_antigravity)
            if self.loaded==self.active and self.profiles.get_profile(self.active) and not self.sessions.in_progress():
                await asyncio.to_thread(self.profiles.save_active_to_profile,self.active)
        finally:
            self.busy=False; self.supervisor.is_restarting=False

    def request_exit(self, expected_profile_id=None):
        if expected_profile_id is not None and expected_profile_id!=self.active:
            raise ValueError('当前选中的配置已变化，请从当前卡片重新退出')
        if self.sessions.in_progress():raise ValueError('登录尚未结束，请完成或取消后再退出配置')
        if self.exit_task and not self.exit_task.done():return self.exit_state
        if self.launch_task and not self.launch_task.done():raise ValueError('正在启动配置，请稍后退出')
        if self.installer.task and not self.installer.task.done():raise ValueError('本体操作尚未完成')
        self.exit_state=dict(phase='queued',message='正在退出当前配置')
        async def run():
            try:await self.session_ops.exit(expected_profile_id)
            except Exception:
                self.exit_state.update(phase='error',message='可能是登录缓存、运行环境或原生保护不兼容，退出未完成；存档保留。')
        self.exit_task=asyncio.create_task(run())
        return self.exit_state
    def status(self):
        p=self.profiles.get_profile(self.active)
        px=self.proxies.get_proxy(p.get('proxy_id')) if p and p.get('proxy_id') else None
        if not p and self.login_environment_open and self.native_runtime_proxy_id:px=self.proxies.get_proxy(self.native_runtime_proxy_id)
        if px: px={k:v for k,v in px.items() if k!='password'}
        port=None if self.busy or self.starting_profile_id or self.native_requires_restart else self.supervisor.active_port
        running=bool(self.supervisor.process and self.supervisor.process.poll() is None)
        if not running:port=None
        return dict(active_profile=p,active_profile_id=self.active,active_proxy=px,engine_running=running,
                    default_profile_id=self.cfg.get('default_profile_id'),antigravity_ready=bool(port),
                    antigravity_port=port,active_port=port,busy=self.busy,last_error=self.last_error,
                    starting_profile_id=self.starting_profile_id,diagnostics=self.diagnostics,
                    privacy=dict(enabled=True,browser_external_connections='blocked',client_ip_headers='removed',profile_homes='shared'),
                    history=self.profiles.history.summary(),
                    common_runtime=self.profiles.native_runtime.status(),suite_version=(SUITE_DIR/'VERSION').read_text().strip(),
                    native_session=dict(state='logged_out' if self.cfg.get('native_logged_out') else 'logged_in' if p else 'unknown'),
                    login_sessions=self.sessions.status(),exit_state=self.exit_state,
                    login_environment_ready=self.login_environment_open and bool(port),
                    opening_login=self.opening_login,
                    logout_protection=self.session_ops.desktop_status,
                    profiles=self.profiles.list_profiles(),proxies=[self.proxies.public_proxy(p) for p in self.proxies.list_proxies()],system=system_memory())

def system_memory():
    try:
        mem={line.split(':')[0]:int(line.split(':')[1].split()[0]) for line in Path('/proc/meminfo').read_text().splitlines()}
        return dict(mem_total='%dMB'%(mem['MemTotal']//1024),mem_used='%dMB'%((mem['MemTotal']-mem['MemAvailable'])//1024),swap_total='%dMB'%(mem['SwapTotal']//1024),swap_used='%dMB'%((mem['SwapTotal']-mem['SwapFree'])//1024))
    except (OSError,KeyError,ValueError): return {}

def session_token(cfg):
    text=cfg['auth_user']+'.'+str(int(time.time()))
    return text+'.'+hmac.new(cfg['session_secret'].encode(),text.encode(),hashlib.sha256).hexdigest()

def valid_session(token,cfg):
    try:
        user,stamp,sig=token.rsplit('.',2)
        age=time.time()-int(stamp)
        text=user+'.'+stamp
        expected=hmac.new(cfg['session_secret'].encode(),text.encode(),hashlib.sha256).hexdigest()
        return user==cfg['auth_user'] and 0<=age<30*86400 and hmac.compare_digest(sig,expected)
    except (ValueError,TypeError,AttributeError): return False

def secure_cookie(request,cfg):
    setting=cfg.get('cookie_secure','auto')
    if isinstance(setting,bool): return setting
    if request.secure: return True
    if cfg.get('trusted_proxy') and request.remote in ('127.0.0.1','::1'):
        return request.headers.get('X-Forwarded-Proto','').split(',')[0].strip()=='https'
    return False

@web.middleware
async def guard(request,handler):
    s=request.app['state']
    public=request.path in ('/__suite/api/login','/__suite/api/auth/google/callback','/__suite/native/control')
    authenticated=valid_session(request.cookies.get('ag_session'),s.cfg)
    if not public and not authenticated:
        if request.path=='/' and request.method=='GET': return web.FileResponse(s.static/'login.html',headers={'Cache-Control':'no-store'})
        return web.json_response({'ok':False,'error':'未登录'},status=401)
    if request.path.startswith('/__suite/api/') and request.method not in ('GET','HEAD','OPTIONS'):
        origin=request.headers.get('Origin')
        if origin and urlsplit(origin).netloc!=request.host: return web.json_response({'ok':False,'error':'请求来源不匹配'},status=403)
        if s.updater.busy() and request.path not in ('/__suite/api/system/update/check','/__suite/api/system/update/start','/__suite/api/login','/__suite/api/logout'):
            return web.json_response(dict(ok=False,error='项目正在更新，请等待完成后再修改配置或启动 IDE'),status=409)
        if s.service_restart.busy() and request.path not in ('/__suite/api/system/service-restart','/__suite/api/login','/__suite/api/logout'):
            return web.json_response(dict(ok=False,error='后台正在重启，请等待恢复'),status=409)
    transport=request.transport
    if authenticated and transport:s.protected_connections.add(transport)
    try: response=await handler(request)
    except (ValueError,KeyError,TypeError,json.JSONDecodeError) as e: return web.json_response({'ok':False,'error':str(e)},status=400)
    except web.HTTPException: raise
    except Exception:
        logging.exception('请求执行失败')
        return web.json_response({'ok':False,'error':'操作失败，请查看服务日志'},status=500)
    finally:
        if transport:s.protected_connections.discard(transport)
    if request.path.startswith('/__suite/'):
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
    return response

async def api(request):
    s=request.app['state']; path=request.match_info['tail']; method=request.method
    d={}
    if method=='POST' and path not in ('profiles/import','profiles/import/preview'):
        if request.can_read_body:
            if request.content_type!='application/json': raise ValueError('请求需要使用 JSON 格式')
            d=await request.json()
            if not isinstance(d,dict): raise ValueError('请求必须是 JSON 对象')
    def ok(**values): return web.json_response(dict(ok=True,**values))
    def require_proxy(px):
        if px and not s.proxies.get_proxy(px): raise ValueError('代理不存在')
    if path=='login' and method=='POST':
        ip=request.remote or 'unknown'; now=time.monotonic()
        attempts=[t for t in s.login_attempts.get(ip,[]) if now-t<60]
        s.login_attempts[ip]=attempts
        if len(attempts)>=10: return web.json_response({'ok':False,'error':'尝试次数过多，请稍后重试'},status=429)
        user=d.get('username','').strip(); password=d.get('password','')
        checked_hash=s.cfg['auth_password_hash']
        valid=await asyncio.to_thread(check_password,password,checked_hash)
        if user!=s.cfg['auth_user'] or not valid:
            attempts.append(now)
            return web.json_response({'ok':False,'error':'用户名或密码错误'},status=401)
        async with s.auth_lock:
            # Password settings can change while the hash is checked off-thread.
            if user!=s.cfg['auth_user'] or checked_hash!=s.cfg['auth_password_hash']:
                attempts.append(now)
                return web.json_response(dict(ok=False,error='用户名或密码错误'),status=401)
            if s.cfg.get('mfa_enabled'):
                from two_factor import check_factor
                code=d.get('code','')
                if not code:
                    attempts.append(now)
                    return web.json_response(dict(ok=False,mfa_required=True,error='请输入验证器验证码，也可使用一条恢复码'),status=401)
                factor=check_factor(s.cfg,code)
                if factor is None:
                    attempts.append(now)
                    return web.json_response(dict(ok=False,mfa_required=True,error='验证码或恢复码不正确，可能已使用或设备时间不一致'),status=401)
                from security_api import commit_security
                commit_security(s,factor)
        s.login_attempts.pop(ip,None)
        response=ok()
        response.set_cookie('ag_session',session_token(s.cfg),httponly=True,samesite='Lax',secure=secure_cookie(request,s.cfg),max_age=30*86400,path='/')
        return response
    if path=='logout' and method=='POST':
        response=ok(); response.del_cookie('ag_session',path='/'); return response
    if path=='status' and method=='GET':
        await s.refresh_runtime_status()
        return web.json_response(s.status())
    if path=='sessions/login-begin' and method=='POST':
        async with s.lock:
            await s.ensure_switch_allowed()
            return ok(intent_id=s.session_ops.begin())
    if path=='sessions/login-complete' and method=='POST':
        async with s.lock:
            profile_id=await s.session_ops.complete(d.get('intent_id',''),d.get('valid_auth',False))
            return ok(profile_id=profile_id)
    if path=='sessions/exit' and method=='POST':return web.json_response(dict(ok=True,state=s.request_exit(d.get('profile_id'))),status=202)
    if path=='system/update/status' and method=='GET': return ok(**s.updater.status())
    if path=='system/update/check' and method=='POST':
        if s.updater.busy():return ok(**s.updater.status())
        return ok(**(await s.updater.check()))
    if path=='system/update/start' and method=='POST':
        async with s.lock:
            await s.ensure_switch_allowed()
            if s.installer.task and not s.installer.task.done():raise ValueError('反重力本体操作中，请等待完成')
            return web.json_response(dict(ok=True,**(await s.updater.start())),status=202)
    if path=='profiles':
        if method=='GET': return web.json_response(s.profiles.list_profiles())
        if method=='POST':
            require_proxy(d.get('proxy_id'))
            async with s.lock: p=await asyncio.to_thread(s.profiles.create_profile,d.get('name',''),d.get('proxy_id',''))
            return web.json_response(p)
        if method=='DELETE':
            p_id=request.query.get('id'); s.profiles.profile_dir(p_id)
            if s.profiles.get_profile(p_id).get('kind')=='existing':raise ValueError('已有共享配置不能删除，原配置由现有反重力使用')
            async with s.lock:
                await s.sync_native_login(force=True)
                if s.active==p_id: await s.stop_and_save(); s.active=None
                await asyncio.to_thread(s.profiles.delete_profile,p_id)
                if s.cfg.get('default_profile_id')==p_id: s.cfg['default_profile_id']=None
                s.persist()
            return ok()
    if path=='profiles/switch' and method=='POST':
        s.request_start(d.get('profile_id'))
        return web.json_response(dict(ok=True,status='starting',profile_id=d.get('profile_id')),status=202)
    if path=='profiles/detect' and method=='POST':
        s.request_detection(d.get('profile_id'))
        return web.json_response(dict(ok=True,status='checking'),status=202)
    if path=='profiles/set-default' and method=='POST':
        p=d.get('profile_id')
        if p: s.profiles.profile_dir(p)
        async with s.lock: s.cfg['default_profile_id']=p; s.persist()
        return ok(default_profile_id=p)
    if path=='profiles/detail' and method=='GET':
        p_id=request.query.get('id'); folder=s.profiles.profile_dir(p_id)
        files=[dict(path=str(p.relative_to(folder/'store')).replace('\\','/'),size=p.stat().st_size) for p in (folder/'store').rglob('*') if p.is_file() and not p.is_symlink()]
        return ok(profile=s.profiles.get_profile(p_id),files=files)
    if path=='profiles/update' and method=='POST':
        if set(d)-{'id','name','proxy_id','email'}:raise ValueError('档案 ID 和创建时间不可修改')
        require_proxy(d.get('proxy_id'))
        async with s.lock:
            p=s.profiles.get_profile(d.get('id'))
            if not p: raise ValueError('档案不存在')
            if s.active==p['id'] and s.supervisor.active_port and d.get('proxy_id',p['proxy_id'])!=p['proxy_id']:
                raise ValueError('修改当前档案代理前，请先停止 IDE')
            p=await asyncio.to_thread(s.profiles.update_profile,p['id'],d.get('name'),d.get('proxy_id'),d.get('email'))
        return ok(profile=p)
    if path=='profiles/file' and method in ('GET','POST'):
        p_id=request.query.get('id') if method=='GET' else d.get('id')
        rel=request.query.get('path','') if method=='GET' else d.get('path','')
        async with s.lock:
            if p_id==s.active and s.supervisor.process and s.supervisor.process.poll() is None:
                raise ValueError('请先停止 IDE，再读取或修改当前档案文件')
            file=s.profiles.resolve_file(p_id,rel)
            runtime_file=s.profiles.file_in_home(p_id,rel)
            if method=='GET':
                if not file.is_file() or file.stat().st_size>1024*1024: raise ValueError('只能编辑 1MB 以内的文本文件')
                return ok(content=file.read_text(encoding='utf-8'))
            content=d.get('content','')
            if not isinstance(content,str) or len(content.encode())>1024*1024: raise ValueError('文件不能超过 1MB')
            if file.suffix=='.json' or file.name=='jetski-standalone-oauth-token': json.loads(content)
            file.parent.mkdir(parents=True,exist_ok=True)
            fd,name=tempfile.mkstemp(dir=file.parent,prefix='.edit-')
            try:
                with os.fdopen(fd,'w',encoding='utf-8') as out: out.write(content); out.flush(); os.fsync(out.fileno())
                os.replace(name,file); os.chmod(file,0o600)
            finally:
                if os.path.exists(name): os.unlink(name)
            # Persistent HOME is authoritative once initialized. Apply the edit there too.
            home=s.profiles.home_for(p_id)
            if runtime_file is not None:
                from account_storage import write_bytes
                spec=s.session_ops.runtime()
                write_bytes(runtime_file,content.encode(),spec.get('native_uid') if spec else None)
        return ok()
    if path=='profiles/import/preview' and method=='POST':
        source=request.query.get('source','suite')
        if source not in ('suite','cpa','sub2api'):raise ValueError('不支持的导入来源')
        async with s.lock:
            s.prune_imports()
            if len(s.pending_imports)>=16:raise ValueError('待导入文件过多，请完成已有导入后重试')
            temporary=s.data/'.pending-imports';temporary.mkdir(parents=True,exist_ok=True,mode=0o700)
            fd,name=tempfile.mkstemp(dir=temporary,prefix='profile-',suffix='.tar.gz');os.close(fd)
            file=Path(name)
            try:
                await save_profile_upload(request,file,max_bytes=4*1024*1024 if source!='suite' else 512*1024*1024)
                if source=='suite':info=await asyncio.to_thread(s.profiles.inspect_import,file)
                else:
                    from external_import import preview_external
                    info=await asyncio.to_thread(preview_external,file,source)
                token=secrets.token_urlsafe(32)
                s.pending_imports[token]=dict(path=str(file),source=source,session=request.cookies.get('ag_session'),expires_at=time.time()+600,has_proxy=info['has_proxy'],has_history=info.get('has_history',False))
            except Exception:
                file.unlink(missing_ok=True);raise
        return ok(import_token=token,**info)
    if path=='profiles/import/confirm' and method=='POST':
        include_proxy=d.get('include_proxy',False)
        if not isinstance(include_proxy,bool):raise ValueError('是否导入对应代理必须是布尔值')
        include_history=d.get('include_history',False);history_mode=d.get('history_mode','merge')
        if not isinstance(include_history,bool):raise ValueError('是否导入聊天文件必须是布尔值')
        if history_mode not in ('merge','replace'):raise ValueError('聊天导入方式必须为合并或覆盖')
        if include_history and history_mode=='replace' and d.get('confirm_replace') is not True:
            raise ValueError('覆盖会替换本机全部聊天记录，请先明确确认')
        async with s.lock:
            s.prune_imports()
            token=d.get('import_token');item=s.pending_imports.get(token)
            if not item or item['session']!=request.cookies.get('ag_session'):raise ValueError('导入预览已失效，请重新选择文件')
            source=item.get('source','suite')
            imported={}
            if source=='suite':
                if include_history and not item['has_history']:raise ValueError('无聊天文件')
                p=await s.import_account(Path(item['path']),include_proxy and item['has_proxy'],include_history,history_mode)
                imported=dict(profiles=[p],imported_count=1,proxy_imported=bool(include_proxy and item['has_proxy']))
            else:
                if include_history:raise ValueError('第三方账号文件不包含聊天记录')
                from external_import import import_external
                imported=await asyncio.to_thread(import_external,Path(item['path']),source,s.profiles)
                p=imported.pop('profile')
            s.pending_imports.pop(token,None);Path(item['path']).unlink(missing_ok=True)
        if source!='suite':
            for profile in imported['profiles']:s.request_detection(profile['id'])
        message='代理由你自己配置' if source!='suite' else '对应代理已导入' if imported['proxy_imported'] else '未导入对应代理' if item['has_proxy'] else '无代理文件'
        history_message='聊天记录已合并导入' if include_history and history_mode=='merge' else '本机聊天记录已替换为导入文件' if include_history else '未导入聊天记录' if item['has_history'] else '无聊天文件'
        return ok(profile=p,has_proxy=item['has_proxy'],source=source,proxy_message=message,history_message=history_message,history_imported=include_history,**imported)
    if path=='profiles/import/cancel' and method=='POST':
        async with s.lock:
            token=d.get('import_token');item=s.pending_imports.get(token)
            if item and item['session']==request.cookies.get('ag_session'):
                Path(item['path']).unlink(missing_ok=True);s.pending_imports.pop(token,None)
        return ok()
    if path=='profiles/import' and method=='POST':
        # Backwards compatible direct imports omit proxies unless explicitly requested.
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'profile.tar.gz'
            await save_profile_upload(request,file)
            include_history=request.query.get('include_history')=='1';mode=request.query.get('history_mode','merge')
            if include_history and mode=='replace' and request.query.get('confirm_replace')!='1':raise ValueError('请明确确认覆盖本机聊天记录')
            async with s.lock:p=await s.import_account(file,request.query.get('include_proxy')=='1',include_history,mode)
        return ok(profile=p)
    if path=='profiles/export' and method=='GET':
        p_id=request.query.get('id'); s.profiles.profile_dir(p_id)
        format=request.query.get('format','suite')
        if format not in ('suite','cpa','sub2api'):raise ValueError('不支持的导出格式')
        async with s.lock:
            await s.ensure_switch_allowed()
            include_proxy=format=='suite' and request.query.get('include_proxy')=='1'
            option=request.query.get('include_history','0')
            if option not in ('0','1'):raise ValueError('是否导出聊天记录必须为 0 或 1')
            include_history=format=='suite' and option=='1'
            profile=s.profiles.get_profile(p_id)
            node=s.proxies.get_proxy(profile.get('proxy_id')) if include_proxy and profile.get('proxy_id') else None
            if include_proxy and not node:raise ValueError('此配置没有可导出的对应代理')
            if p_id==s.active and not s.sessions.in_progress():await asyncio.to_thread(s.profiles.save_active_to_profile,p_id)
            if format!='suite':
                from external_import import export_external
                exported=await asyncio.to_thread(export_external,s.profiles,p_id,format)
                data=json.dumps(exported,ensure_ascii=False,indent=2).encode('utf-8')
            else:
                with tempfile.TemporaryDirectory() as tmp:
                    file=Path(tmp)/'profile.tar.gz'
                    await asyncio.to_thread(s.profiles.export_profile,p_id,file,node,include_history)
                    data=await asyncio.to_thread(file.read_bytes)
        suffix='tar.gz' if format=='suite' else 'json'
        content_type='application/gzip' if format=='suite' else 'application/json'
        return web.Response(body=data,content_type=content_type,headers={'Content-Disposition':'attachment; filename="%s_%s.%s"'%(format,p_id,suffix),'Cache-Control':'no-store'})
    if path=='proxies':
        if method=='GET': return web.json_response([{k:v for k,v in p.items() if k!='password'} for p in s.proxies.list_proxies()])
        if method=='POST':
            async with s.lock: p=await asyncio.to_thread(s.proxies.add_from_input,d)
            return ok(proxy={k:v for k,v in p.items() if k!='password'})
        if method=='DELETE':
            px=request.query.get('id')
            async with s.lock:
                if any(p.get('proxy_id')==px for p in s.profiles.list_profiles()): raise ValueError('代理仍绑定在档案中，请先解除绑定')
                s.proxies.delete_proxy(px)
            return ok()
    if path=='proxies/detail' and method=='GET':
        async with s.lock:
            item=s.proxies.get_proxy(request.query.get('id'))
            if not item:raise ValueError('代理节点不存在')
            proxy=s.proxies.public_proxy(item,reveal_password=request.query.get('reveal_password')=='1')
            if request.query.get('include_link')=='1':proxy['link']=s.proxies.get_proxy_link(item)
            bound=[dict(id=p['id'],name=p['name']) for p in s.profiles.list_profiles() if p.get('proxy_id')==item['id']]
        return ok(proxy=proxy,bound_profiles=bound)
    if path=='proxies/update' and method=='POST':
        proxy_id=d.get('id')
        fields={key:value for key,value in d.items() if key!='id'}
        async with s.lock:
            await s.ensure_switch_allowed()
            candidate=s.proxies.prepare_update(proxy_id,fields)
            item=s.proxies.get_proxy(proxy_id)
            connection_changed=any((s.proxies.normalize_protocol(item.get(key))!=s.proxies.normalize_protocol(candidate.get(key)))
                                   if key=='protocol' else item.get(key)!=candidate.get(key)
                                   for key in ('protocol','host','port','username','password'))
            active=s.profiles.get_profile(s.active)
            running=bool(s.supervisor.process and s.supervisor.process.poll() is None)
            if connection_changed and running and active and active.get('proxy_id')==proxy_id:
                return web.json_response(dict(ok=False,error='此节点正被当前 IDE 使用。请先停止 IDE，再修改连接信息；节点 ID 和绑定保持原值。',requires_stop=True),status=409)
            affects_cli=bool(connection_changed and active and active.get('proxy_id')==proxy_id)
            previous=dict(item)
            if affects_cli:
                native=s.session_ops.runtime()
                if native:await asyncio.to_thread(s.profiles.cli_credentials.stop,native)
            updated=await asyncio.to_thread(s.proxies.update_proxy,proxy_id,fields)
            if affects_cli:
                try:await s.prepare_engine_proxy(active)
                except Exception:
                    item.clear();item.update(previous);s.proxies._save()
                    try:await s.prepare_engine_proxy(active)
                    except Exception:pass
                    raise ValueError('可能是共用代理桥重建失败，节点原配置已恢复，请检查后台运行状态')
            if connection_changed:
                for p in s.profiles.list_profiles():
                    if p.get('proxy_id')==proxy_id:s.diagnostics.pop(p['id'],None)
                s._auth_cache=None
        return ok(proxy=s.proxies.public_proxy(updated),connection_changed=connection_changed)
    if path=='proxies/test' and method=='POST': return web.json_response(await asyncio.to_thread(s.proxies.test_proxy,d.get('proxy_id')))
    if path=='proxies/import-links' and method=='POST':
        async with s.lock: nodes=await asyncio.to_thread(s.proxies.import_links,d.get('links',''))
        return ok(proxies=[{k:v for k,v in node.items() if k!='password'} for node in nodes])
    if path=='system/core-status' and method=='GET':
        await s.refresh_runtime_status()
        proc=s.supervisor.process
        file=Path(s.cfg['antigravity_bin'])
        installed=bool(s.discovered['binary']) if s.discovery_enabled else s.supervisor.installed()
        return ok(installed=installed,usable=installed,button_label='卸载反重力' if installed else '安装反重力',binary_path=s.discovered['binary'] if s.discovery_enabled else str(file),
            installations=s.discovered['installations'],size_mb=round(file.stat().st_size/1024/1024,1) if file.exists() else 0,running=bool(proc and proc.poll() is None),active_port=s.supervisor.active_port,install_state=s.installer.state,managed=s.installer.managed_directory() in file.resolve().parents)
    if path=='system/discover' and method=='POST':
        await s.refresh_existing()
        return ok(discovered=s.discovered)
    if path=='system/auth-status' and method=='GET':
        from auth_probe import auth_status
        return ok(**(await auth_status(s,force=request.query.get('force')=='1')))
    if path=='system/install-core' and method=='POST':
        async with s.lock:
            await s.ensure_switch_allowed()
            s.installer.start()
        return ok(state=s.installer.state,message='安装任务已提交')
    if path=='system/uninstall-core' and method=='POST':
        async with s.lock:
            if s.launch_task and not s.launch_task.done():raise ValueError('正在启动档案，请等待完成')
            if s.installer.task and not s.installer.task.done():raise ValueError('已有本体操作正在进行')
            await s.stop_and_save()
            if s.discovery_enabled:
                from discovery import engine_processes
                from process_tracking import ExistingProcess,stop_process
                binary=Path(s.supervisor.antigravity_bin).resolve()
                for info in await asyncio.to_thread(engine_processes):
                    if Path(info['executable']).resolve()==binary:await asyncio.to_thread(stop_process,ExistingProcess(info))
            s.installer.start_uninstall()
        return web.json_response(dict(ok=True,state=s.installer.state),status=202)
    if path=='system/install-core/status' and method=='GET': return ok(state=s.installer.state)
    if path=='system/stop' and method=='POST':
        async with s.lock:
            await s.ensure_switch_allowed()
            await s.stop_and_save()
            s.cfg['engine_stopped']=True;s.login_environment_open=False;s.persist()
        return ok()
    if path=='system/restart' and method=='POST':
        s.request_start(s.active,restart=True)
        return web.json_response(dict(ok=True,status='starting'),status=202)
    if path=='system/start' and method=='POST':
        if s.cfg.get('native_logged_out') or (not s.active and not s.cfg.get('default_profile_id')):s.request_open_login()
        else:s.request_start(s.active or s.cfg.get('default_profile_id'))
        return web.json_response(dict(ok=True,status='starting'),status=202)
    if path=='system/service-restart/status' and method=='GET':return ok(**s.service_restart.status())
    if path=='system/service-restart' and method=='POST':
        return web.json_response(dict(ok=True,**await s.service_restart.start()),status=202)
    if path=='system/config':
        if method=='GET': return ok(**{k:s.cfg.get(k) for k in ['listen_host','listen_port','auth_user']})
        if method=='POST':
            async with s.lock,s.auth_lock:
                port=d.get('listen_port',s.cfg['listen_port'])
                if not 1<=int(port)<=65535: raise ValueError('端口必须在 1–65535 范围内')
                changed_user=d.get('auth_user',s.cfg['auth_user']).strip()
                if not changed_user: raise ValueError('用户名不能为空')
                password=d.get('auth_pass')
                if password and len(password)<12: raise ValueError('新密码至少需要 12 个字符')
                from security_api import commit_security
                updates={}
                if password or changed_user!=s.cfg['auth_user']:
                    from security_api import verify_current
                    updates.update(await verify_current(s,request,d))
                    updates['session_secret']=secrets.token_hex(32)
                if password:updates['auth_password_hash']=await asyncio.to_thread(hash_password,password)
                changed=int(port)!=s.cfg['listen_port'];updates.update(listen_port=int(port),auth_user=changed_user)
                commit_security(s,updates,keep_transport=request.transport)
            return ok(restart_required=changed)
    if path.startswith('system/security/'):
        from security_api import security_api
        return await security_api(request,s,path,d)
    if path.startswith('auth/google/'):
        from oauth import google_api
        return await google_api(request,s,path,d)
    raise web.HTTPNotFound()

async def save_profile_upload(request,file,max_bytes=512*1024*1024):
    if request.content_type!='multipart/form-data':raise ValueError('请上传配置压缩包')
    reader=await request.multipart();field=await reader.next()
    if not field or field.name!='file':raise ValueError('请上传 file 字段')
    size=0
    with Path(file).open('wb') as output:
        os.chmod(file,0o600)
        while True:
            chunk=await field.read_chunk()
            if not chunk:break
            size+=len(chunk)
            if size>max_bytes:raise ValueError('上传文件超过 %dMB 限制'%(max_bytes//1024//1024))
            output.write(chunk)
    if not size:raise ValueError('上传文件为空')

async def page(request):
    s=request.app['state']
    if request.path=='/__suite/native/control':
        if request.method!='POST' or request.content_type!='application/json':raise web.HTTPBadRequest()
        data=await request.json()
        if not isinstance(data,dict):raise web.HTTPBadRequest()
        spec=s.native_controls.authorize(request,data)
        if not spec:raise web.HTTPForbidden()
        if s.updater.busy() or s.service_restart.busy():return web.json_response(dict(ok=False,error='项目正在更新或后台正在重启'),status=409)
        operation=data.get('operation');fields=data.get('fields',{})
        if not isinstance(fields,dict):raise web.HTTPBadRequest()
        if operation=='status':
            await s.refresh_runtime_status()
            return web.json_response(dict(ok=True,**s.native_controls.minimal_status()))
        if operation=='ready':
            current=s.session_ops.runtime()
            if not current or current.get('id')!=spec['id']:raise web.HTTPForbidden()
            s.session_ops.desktop_status['renderer_ready']=True
            s.session_ops.desktop_status['renderer_guard_version']=fields.get('guard_version',1)
            return web.json_response(dict(ok=True))
        current=s.session_ops.runtime()
        if current and current.get('id')!=spec['id']:return web.json_response(dict(ok=False,error='此窗口不是当前配置的运行环境，请返回配置面板'),status=409)
        if operation=='auth-status':
            from auth_probe import auth_status
            return web.json_response(dict(ok=True,**(await auth_status(s))))
        if operation=='begin':
            async with s.lock:
                await s.ensure_switch_allowed()
                return web.json_response(dict(ok=True,intent_id=s.session_ops.begin(spec,'native')))
        if operation=='complete':
            async with s.lock:
                identifier=await s.session_ops.complete(fields.get('intent_id',''),fields.get('valid_auth',False))
                return web.json_response(dict(ok=True,profile_id=identifier))
        if operation=='exit':return web.json_response(dict(ok=True,state=s.request_exit(fields.get('profile_id'))),status=202)
        if operation=='switch':
            identifier=fields.get('profile_id')
            if not any(p['id']==identifier for p in s.profiles.list_profiles()):raise web.HTTPBadRequest()
            s.request_start(identifier);return web.json_response(dict(ok=True),status=202)
        if operation=='restart':s.request_start(s.active,restart=True);return web.json_response(dict(ok=True),status=202)
        raise web.HTTPBadRequest()
    if request.path=='/__suite/floating.js': return web.FileResponse(s.static/'floating.js')
    if request.path=='/__suite/privacy.js': return web.FileResponse(s.static/'privacy.js')
    if request.path=='/__suite/proxy-editor.js': return web.FileResponse(s.static/'proxy-editor.js')
    if request.path=='/__suite/security.js': return web.FileResponse(s.static/'security.js')
    if request.path=='/__suite/portal-ui.js': return web.FileResponse(s.static/'portal-ui.js')
    if request.path=='/__suite/portal.js': return web.FileResponse(s.static/'portal.js')
    if request.path=='/__suite/transfer-feedback.js': return web.FileResponse(s.static/'transfer-feedback.js')
    if request.path=='/__suite/core-toggle.js': return web.FileResponse(s.static/'core-toggle.js')
    if request.path=='/__suite/service-restart.js': return web.FileResponse(s.static/'service-restart.js')
    if request.path=='/__suite/update.js': return web.FileResponse(s.static/'update.js')
    if request.path=='/__suite/logout-guard.js': return web.FileResponse(s.static/'logout-guard.js')
    if request.path=='/__suite/stream-transport.js': return web.FileResponse(s.static/'stream-transport.js')
    if request.path=='/__suite/ide-ready.js': return web.FileResponse(s.static/'ide-ready.js')
    if request.path=='/__suite/runtime-watch.js': return web.FileResponse(s.static/'runtime-watch.js')
    if request.path=='/__suite/native-stream':
        from stream_transport import subscription
        return await subscription(request)
    if request.path=='/__suite/portal.css': return web.FileResponse(s.static/'portal.css')
    if request.path=='/__suite/portal' or (request.path=='/' and 'portal' in request.query):
        return web.FileResponse(s.static/'portal.html',headers={'Cache-Control':'no-store'})
    if request.path.startswith('/__suite/'): raise web.HTTPNotFound()
    if request.path=='/' and request.method in ('GET','HEAD'):
        await s.refresh_runtime_status()
        if not s.supervisor.active_port or not (s.supervisor.process and s.supervisor.process.poll() is None):
            raise web.HTTPFound('/?portal=1')
    if s.native_requires_restart or (s.cfg.get('native_logged_out') and not s.login_environment_open):
        if request.method in ('GET','HEAD'):raise web.HTTPFound('/?portal=1')
        return web.json_response(dict(ok=False,error='当前原生会话未登录，或需要按该配置的代理重新启动，请在配置页面选择账号'),status=409)
    return await proxy(request)

async def resources(app):
    s=app['state']
    s.http=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None,sock_connect=10),
                                 auto_decompress=False,cookie_jar=aiohttp.DummyCookieJar(),trust_env=False)
    s.stream_http=aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=64),timeout=aiohttp.ClientTimeout(total=None,sock_connect=10),
                                 auto_decompress=False,cookie_jar=aiohttp.DummyCookieJar(),trust_env=False)
    boot=None
    if s.discovery_enabled:
        await s.refresh_existing(import_logins=True)
        if not s.updater.busy():await s.session_ops.protect_existing()
        if not s.updater.busy():await s.initialize_account_layout()
        if not s.updater.busy() and s.profiles.get_profile(s.active):
            await s.prepare_engine_proxy(s.profiles.get_profile(s.active))
        elif not s.updater.busy() and s.session_ops.runtime():
            await s.prepare_engine_proxy(dict(id=None,proxy_id=s.cfg.get('login_proxy_id')))
        s.discovery_running=True
        async def scan_existing():
            while s.discovery_running:
                await asyncio.sleep(3)
                if s.busy or s.starting_profile_id or (s.installer.task and not s.installer.task.done()):continue
                try:await s.refresh_existing()
                except Exception:logging.error('已有反重力配置扫描失败，请检查目录权限')
        s.discovery_task=asyncio.create_task(scan_existing())
    async def autoboot():
        try:
            if s.cfg.get('login_environment') and s.sessions.state.get('waiting'):
                if s.cfg.get('engine_stopped'):return
                s.request_open_login();return
            if s.cfg.get('native_logged_out'):return
            resumed=s.cfg.pop('restart_resume_profile_id',None)
            if resumed:s.persist()
            if s.cfg.get('engine_stopped') and not resumed:return
            target=resumed or s.cfg.get('default_profile_id')
            common=s.profiles.native_runtime
            migrate=bool(common.selected and target and (not common.binding or common.binding['profile_id']!=target))
            if target and (not s.supervisor.active_port or migrate):s.request_start(target,restart=migrate)
        except Exception as e: s.last_error=str(e); logging.error('默认档案启动失败：%s',e)
    if s.cfg.get('default_profile_id') or s.cfg.get('restart_resume_profile_id'): boot=asyncio.create_task(autoboot())
    elif s.cfg.get('login_environment'):boot=asyncio.create_task(autoboot())
    yield
    s.discovery_running=False
    if s.discovery_task:
        s.discovery_task.cancel();await asyncio.gather(s.discovery_task,return_exceptions=True)
    if boot and not boot.done():
        await boot
    if s.launch_task and not s.launch_task.done(): await s.launch_task
    if s.exit_task and not s.exit_task.done():await s.exit_task
    for task in s.detection_tasks.values():
        if not task.done(): task.cancel()
    await asyncio.gather(*s.detection_tasks.values(),return_exceptions=True)
    if s.installer.task and not s.installer.task.done():
        s.installer.task.cancel(); await asyncio.gather(s.installer.task,return_exceptions=True)
    async with s.lock:
        await s.stop_and_save()
        await asyncio.to_thread(s.supervisor.close)
    await s.http.close()
    await s.stream_http.close()
    if s.outbound_bridge: await s.outbound_bridge.close()
    for item in s.pending_imports.values():Path(item['path']).unlink(missing_ok=True)
    s.pending_imports.clear()
    s.pending_mfa.clear()

async def shutting_down(app):
    s=app['state']
    if s.profiles.cli_proxy:s.profiles.cli_proxy.suspend()
    s.supervisor._running=False
    s.supervisor.is_restarting=True
    await asyncio.gather(*[ws.close(code=1001,message=b'backend restarting') for ws in list(getattr(s,'native_stream_sockets',set()))],return_exceptions=True)

def create_app(cfg=None,profile_mgr=None,supervisor=None):
    cfg=cfg or load_config()
    app=web.Application(middlewares=[privacy_guard,guard],client_max_size=512*1024*1024)
    app['state']=SuiteState(cfg,profile_mgr,supervisor)
    app.router.add_route('*','/__suite/api/{tail:.*}',api)
    app.router.add_route('*','/{path:.*}',page)
    app.cleanup_ctx.append(resources)
    app.on_shutdown.append(shutting_down)
    return app

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    config=load_config()
    web.run_app(create_app(config),host=config['listen_host'],port=int(config['listen_port']),access_log=None)
