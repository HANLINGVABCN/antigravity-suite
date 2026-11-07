import logging
import os
import re
import signal
import socket
import subprocess
import threading
import time
import tempfile
import hashlib
from pathlib import Path
from discovery import engine_processes,config_dir_for
from process_tracking import ExistingProcess,stop_process,process_ports

class ProcessSupervisor:
    def __init__(self,antigravity_bin='/opt/Antigravity/antigravity',display=':99',log_path=None,runtime_home=None):
        self.antigravity_bin=antigravity_bin
        self.display=display
        self.runtime_home=Path(runtime_home or Path.home())
        self.log_path=Path(log_path or self.runtime_home/'.config/Antigravity/logs/main.log')
        self.process=None; self.active_port=None; self.current_proxy_url=None
        self.is_restarting=False; self.last_error=''; self._running=True
        self._lock=threading.RLock(); self._monitor_thread=None; self._xvfb=None
        self._temp=None
        self.native=False;self.run_uid=None;self.session_env={};self.config_dir=self.runtime_home/'.config/Antigravity'
    def set_home(self, home):
        self.runtime_home=Path(home)
        self.native=False;self.run_uid=None;self.session_env={}
        self.config_dir=self.runtime_home/'.config/Antigravity'
        self.log_path=self.config_dir/'logs/main.log'
    def configure_native(self, profile):
        self.runtime_home=Path(profile['native_home']);self.config_dir=Path(profile['native_config_dir'])
        self.log_path=self.config_dir/'logs/main.log';self.native=True
        self.run_uid=profile['native_uid'];self.display=profile['native_display']
        self.session_env=profile.get('native_env',{})
    def find_existing(self):
        for info in engine_processes():
            if info['env'].get('HOME')==str(self.runtime_home) and config_dir_for(info)==self.config_dir.resolve():
                if self.run_uid is None or info['uid']==self.run_uid:return info
        return None
    def adopt_existing(self):
        with self._lock:
            if self.process and self.process.poll() is None:return True
            existing=self.find_existing()
            if not existing:return False
            self.process=ExistingProcess(existing);self.active_port=self.detect_port_from_log()
            self.current_proxy_url=None
            self._start_monitor()
            return True
    def _start_monitor(self):
        if not self._monitor_thread or not self._monitor_thread.is_alive():
            self._monitor_thread=threading.Thread(target=self._monitor_loop,daemon=True);self._monitor_thread.start()
    def installed(self):
        return Path(self.antigravity_bin).is_file() and os.access(self.antigravity_bin,os.X_OK)
    def ensure_xvfb(self):
        if self._xvfb and self._xvfb.poll() is None: return
        try:
            environment=dict(os.environ,**self.session_env)
            r=subprocess.run(['xdpyinfo','-display',self.display],env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=3)
            if r.returncode==0: return
        except FileNotFoundError: pass
        self._xvfb=subprocess.Popen(['Xvfb',self.display,'-screen','0','1280x800x24','-nolisten','tcp'],
                                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        time.sleep(.6)
        if self._xvfb.poll() is not None: raise RuntimeError('虚拟显示器启动失败，请检查 Xvfb 和 DISPLAY')
    def detect_port_from_log(self):
        if not self.process or self.process.poll() is not None or not self.log_path.exists(): return None
        with self.log_path.open('rb') as f:
            f.seek(max(0,self.log_path.stat().st_size-131072))
            content=f.read().decode('utf-8',errors='ignore')
        owned=process_ports(self.process.pid)
        for text in reversed(re.findall(r'https://127\.0\.0\.1:(\d+)/',content)):
            p=int(text)
            if not 1<=p<=65535 or p not in owned: continue
            with socket.socket() as s:
                s.settimeout(.15)
                if s.connect_ex(('127.0.0.1',p))==0: return p
        return None
    def start_antigravity(self,proxy_url=None):
        with self._lock:
            if self.process and self.process.poll() is None: return self.active_port
            if self.adopt_existing():
                for _ in range(30):
                    self.active_port=self.detect_port_from_log()
                    if self.active_port:return self.active_port
                    time.sleep(.3)
                raise RuntimeError('已有反重力实例未就绪；请查看它的原生运行日志')
            if not self.installed(): raise RuntimeError('反重力本体尚未安装')
            self.ensure_xvfb()
            self.runtime_home.mkdir(parents=True,exist_ok=True,mode=0o700)
            env=os.environ.copy()
            if self.native:env.update(self.session_env)
            if self._temp:self._temp.cleanup()
            # Chromium uses a Unix socket here; deeply nested HOME paths exceed 108 bytes.
            label=hashlib.sha256(str(self.runtime_home).encode()).hexdigest()[:8]
            self._temp=tempfile.TemporaryDirectory(prefix='agy-'+label+'-',dir='/tmp')
            env.update(HOME=str(self.runtime_home),DISPLAY=self.display,
                       XDG_CONFIG_HOME=str(self.config_dir.parent),XDG_DATA_HOME=str(self.runtime_home/'.local/share'),
                       XDG_CACHE_HOME=str(self.runtime_home/'.cache'),TMPDIR=self._temp.name)
            (self.runtime_home/'workspace').mkdir(exist_ok=True,mode=0o700)
            user_arguments={}
            if self.run_uid is not None and self.run_uid!=os.geteuid():
                import pwd
                account=pwd.getpwuid(self.run_uid)
                user_arguments=dict(user=self.run_uid,group=account.pw_gid,extra_groups=[])
                env['USER']=env['LOGNAME']=account.pw_name
                os.chown(self._temp.name,self.run_uid,account.pw_gid)
                workspace=self.runtime_home/'workspace'
                if workspace.stat().st_uid==os.geteuid():os.chown(workspace,self.run_uid,account.pw_gid)
            for k in ['http_proxy','https_proxy','all_proxy','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY']: env.pop(k,None)
            env['no_proxy']=env['NO_PROXY']='localhost,127.0.0.1,::1'
            cmd=[self.antigravity_bin,'--no-sandbox','--disable-gpu','--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
                 '--user-data-dir='+str(self.config_dir)]
            if proxy_url:
                for k in ['http_proxy','https_proxy','all_proxy','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY']: env[k]=proxy_url
                cmd.append('--proxy-server='+proxy_url)
            log=self.runtime_home/'antigravity-suite-engine.log' if self.native else self.runtime_home/'engine.log'
            fd=os.open(log,os.O_CREAT|os.O_APPEND|os.O_WRONLY,0o600)
            with os.fdopen(fd,'ab') as out:
                self.process=subprocess.Popen(cmd,env=env,cwd=self.runtime_home/'workspace',stdout=out,stderr=out,start_new_session=True,**user_arguments)
            self.current_proxy_url=proxy_url; self.active_port=None; self.last_error=''
            for _ in range(90):
                if self.process.poll() is not None:
                    self.last_error='反重力进程退出，请查看 '+str(log)
                    raise RuntimeError(self.last_error)
                port=self.detect_port_from_log()
                if port:
                    self.active_port=port
                    self._start_monitor()
                    return port
                time.sleep(.5)
            self.last_error='启动超时，未检测到 Web IDE 端口；请查看 '+str(log)
            self.stop_antigravity()
            raise RuntimeError(self.last_error)
    def stop_antigravity(self):
        with self._lock:
            p=self.process
            if p is None:
                existing=self.find_existing()
                if existing:p=ExistingProcess(existing)
            self.process=None;self.active_port=None
            stop_process(p)
            if self._temp:self._temp.cleanup();self._temp=None
    def restart(self,proxy_url=None):
        self.is_restarting=True
        try:
            self.stop_antigravity(); return self.start_antigravity(proxy_url)
        finally: self.is_restarting=False
    def _monitor_loop(self):
        while self._running:
            with self._lock:
                if not self._running: break
                if not self.is_restarting and self.process:
                    if self.process.poll() is not None:
                        self.active_port=None
                        try: self.start_antigravity(self.current_proxy_url)
                        except Exception:
                            logging.error('核心引擎自动重启失败；请检查 engine.log')
                            # Avoid continuous auth/reconnect loops when the engine fails.
                            self.process=None
                    else: self.active_port=self.detect_port_from_log()
            time.sleep(1)
    def close(self):
        self._running=False
        self.stop_antigravity()
        if self._xvfb and self._xvfb.poll() is None:
            try: os.killpg(self._xvfb.pid,signal.SIGTERM); self._xvfb.wait(timeout=3)
            except (ProcessLookupError,subprocess.TimeoutExpired): pass
