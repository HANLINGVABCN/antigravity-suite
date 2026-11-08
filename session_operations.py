"""Local session exit: stop, preserve archives, clear only working login, reopen empty UI."""
import asyncio
import copy
import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

from config import atomic_json
from native_runtime import own_tree


class SessionOperations:
    def __init__(self,state):
        self.state=state
        self.desktop_status=dict(installed=False,reason='尚未检查原生退出保护')

    def runtime(self,create=False):
        s=self.state;native=s.profiles.native_runtime
        spec=native.binding['native'] if native.binding else s.sessions.state.get('working') or native.selected
        if spec:
            native.validate(spec)
            return dict(spec)
        if not create:return None
        home=s.data/'login-environment/home';home.mkdir(parents=True,exist_ok=True,mode=0o700)
        return dict(id=hashlib.sha256(str(home.resolve()).encode()).hexdigest()[:32],runtime_kind='internal-login',
            native_home=str(home.resolve()),native_config_dir=str(home.resolve()/'.config/Antigravity'),
            native_uid=os.geteuid() if hasattr(os,'geteuid') else 0,native_binary=s.cfg['antigravity_bin'],
            native_display=s.cfg['display_num'],native_env={},available=True)

    def publish_controls(self,spec):
        return self.state.native_controls.publish(spec)

    async def ensure_desktop_guard(self):
        s=self.state
        from process_supervisor import ProcessSupervisor
        if not isinstance(s.supervisor,ProcessSupervisor):return self.desktop_status
        if not Path(s.cfg['antigravity_bin']).is_file():return self.desktop_status
        from desktop_guard import install
        running=bool(s.supervisor.process and s.supervisor.process.poll() is None)
        self.desktop_status=await asyncio.to_thread(install,s.cfg['antigravity_bin'],s.data,running)
        return self.desktop_status

    async def protect_existing(self):
        s=self.state
        try:
            await self.ensure_desktop_guard()
        except ValueError as error:
            if '停止' not in str(error):
                self.desktop_status=dict(installed=False,reason=str(error));return
            if s.cfg.get('engine_stopped'):
                self.desktop_status=dict(installed=False,reason='反重力已停止，若有手动打开的实例需关闭后才能更新桌面保护');return
            if s.active:
                await s.activate(s.active)
            else:
                spec=self.runtime()
                if not spec:self.desktop_status=dict(installed=False,reason=str(error));return
                if hasattr(s.supervisor,'configure_native'):s.supervisor.configure_native(spec)
                if hasattr(s.supervisor,'adopt_existing'):await asyncio.to_thread(s.supervisor.adopt_existing)
                await asyncio.to_thread(s.supervisor.stop_antigravity)
                await self.start_empty(spec)
                s.cfg['login_environment']=True;s.persist()
        except (OSError,KeyError):
            self.desktop_status=dict(installed=False,reason='可能是本体权限或资源不兼容，原生退出保护尚未安装')

    async def start_empty(self,spec):
        s=self.state
        s.profiles.native_runtime.selected=spec
        s.profiles._set_home(Path(spec['native_home']));s.profiles.bound_profile=None
        if hasattr(s.supervisor,'configure_native'):s.supervisor.configure_native(spec)
        elif hasattr(s.supervisor,'set_home'):s.supervisor.set_home(spec['native_home'])
        self.publish_controls(spec)
        await self.ensure_desktop_guard()
        proxy_id=s.native_runtime_proxy_id or s.cfg.get('login_proxy_id')
        old=s.proxies.get_proxy(proxy_id) if proxy_id else None
        if proxy_id and not old:raise ValueError('未登录环境的代理已不存在，请先恢复节点')
        s.native_runtime_proxy_id=proxy_id or None
        s.engine_proxy_url=await s.prepare_engine_proxy(dict(id=None,proxy_id=proxy_id))
        await asyncio.to_thread(s.supervisor.start_antigravity,s.engine_proxy_url)
        s.login_environment_open=True;s.cfg['engine_stopped']=False;s.persist()

    async def exit(self,expected_profile_id=None):
        s=self.state
        async with s.lock:
            if s.sessions.in_progress():raise ValueError('登录尚未结束，请完成或取消后再退出配置')
            current=self.runtime()
            if current:await asyncio.to_thread(s.profiles.cli_credentials.stop,current)
            await s.sync_native_login(force=True)
            if expected_profile_id is not None and expected_profile_id!=s.active:raise ValueError('当前配置已变化，请从当前选中卡片重新退出')
            old_id=s.active;old_loaded=s.loaded;old_profile=s.profiles.get_profile(old_id)
            old_requires_restart=s.native_requires_restart
            old_sessions=copy.deepcopy(s.sessions.state)
            old_binding=copy.deepcopy(s.profiles.native_runtime.binding)
            old_flags={key:s.cfg.get(key) for key in ('native_logged_out','login_environment','login_proxy_id')}
            was_open=s.login_environment_open
            was_running=bool(s.supervisor.process and s.supervisor.process.poll() is None)
            spec=self.runtime(create=True)
            if not s.supervisor.installed():raise ValueError('反重力本体尚未安装')
            s.busy=True;s.supervisor.is_restarting=True
            s.exit_state.update(phase='saving',message='正在停止运行并保存当前登录存档')
            backup=s.data/'session-exit-backups'/str(time.time_ns());backup.mkdir(parents=True,mode=0o700)
            moved=[]
            try:
                await asyncio.to_thread(s.supervisor.stop_antigravity)
                await s.sync_native_login(force=True)
                old_id=s.active;old_loaded=s.loaded;old_profile=s.profiles.get_profile(old_id)
                if old_profile and old_loaded==old_id:
                    await asyncio.to_thread(s.profiles.save_active_to_profile,old_id)
                # Never clear an archive HOME. Without an existing native HOME, use a dedicated empty environment.
                if spec.get('runtime_kind')=='profile-local':
                    spec=None
                if spec is None:spec=self.runtime(create=True)
                s.profiles.native_runtime.validate(spec)
                token=Path(spec['native_home'])/'.gemini/jetski-standalone-oauth-token'
                if not token.resolve().is_relative_to(Path(spec['native_home']).resolve()):raise ValueError('当前登录路径指向运行目录以外')
                if token.exists():
                    if token.is_symlink():raise ValueError('当前登录文件不能是符号链接')
                    target=backup/'jetski-standalone-oauth-token';shutil.copy2(token,target)
                    token.unlink();moved.append((token,target))
                atomic_json(backup/'context.json',dict(profile_id=old_id,runtime=spec))
                s.active=None;s.loaded=None;s.profiles.native_runtime.detach();s.profiles.native_runtime.clear_ignored_login()
                s.sessions.exited(spec);s.cfg['native_logged_out']=True;s.cfg['login_environment']=True
                if not was_running and old_profile:s.native_runtime_proxy_id=old_profile.get('proxy_id') or None
                s.cfg['login_proxy_id']=s.native_runtime_proxy_id
                s.native_requires_restart=False;s.persist()
                s.exit_state.update(phase='opening',message='正在打开未登录界面')
                await self.start_empty(spec)
                from auth_probe import auth_status
                context=SimpleNamespace(cfg=s.cfg,supervisor=s.supervisor,http=s.http,active=None,busy=False)
                auth=await auth_status(context,force=True)
                if auth.get('token_present') is not False:raise ValueError('未能确认当前登录状态已清除，已保留原存档')
                s.exit_state.update(phase='ready',message='当前配置已退出，存档保留。可以重新登录。')
            except Exception:
                await asyncio.to_thread(s.supervisor.stop_antigravity)
                for target,saved in moved:
                    target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(saved,target);own_tree(target,spec['native_uid'])
                s.login_environment_open=was_open
                s.sessions.state=old_sessions;s.sessions.save()
                s.profiles.native_runtime.binding=old_binding
                if old_binding:atomic_json(s.profiles.native_runtime.file,old_binding)
                else:s.profiles.native_runtime.file.unlink(missing_ok=True)
                for key,value in old_flags.items():
                    if value is None:s.cfg.pop(key,None)
                    else:s.cfg[key]=value
                if old_profile:
                    if spec.get('runtime_kind')=='internal-login':
                        s.profiles.native_runtime.detach();s.profiles.native_runtime.selected=spec
                        await asyncio.to_thread(s.profiles.native_runtime.activate,old_id)
                    else:s.profiles.native_runtime.associate(old_id,spec)
                    s.bind_engine_home(old_id)
                    s.active=old_id;s.loaded=old_loaded
                    s.native_requires_restart=old_requires_restart
                    if was_running:
                        await s.prepare_engine_proxy(old_profile)
                        await asyncio.to_thread(s.supervisor.start_antigravity,s.engine_proxy_url)
                else:
                    s.active=old_id;s.loaded=old_loaded
                    if was_running:await self.start_empty(spec)
                s.persist()
                s.exit_state.update(phase='error',message='可能是运行环境、登录缓存或原生保护不兼容，退出未完成；原存档保留。')
                raise
            finally:
                s.busy=False;s.supervisor.is_restarting=False

    def begin(self,spec=None,source='web'):
        s=self.state;spec=spec or self.runtime()
        if not spec:raise ValueError('请先打开反重力的未登录界面')
        token=s.profiles.installed_token(Path(spec['native_home'])/'.gemini/jetski-standalone-oauth-token')
        s.sessions.state['working']=spec;s.profiles.native_runtime.clear_ignored_login()
        return s.sessions.begin(source,token)

    async def complete(self,nonce,valid_auth):
        s=self.state
        saved=s.sessions.complete(nonce,valid_auth)
        if not saved and valid_auth is True:
            await s.sync_native_login(force=True)
            saved=s.sessions.state['completed'].get(nonce)
        return saved
