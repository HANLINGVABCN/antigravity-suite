"""Authenticated management restart via a detached, fixed-operation worker."""
import asyncio
import json
import os
import secrets
import sys
import time
from pathlib import Path

from config import SUITE_DIR,atomic_json
from service_control import launch_worker

BUSY={'queued','stopping','starting','recovering'}


class ServiceRestart:
    def __init__(self,state):
        self.state=state;self.root=state.data/'service-restarts';self.file=self.root/'status.json'
        self.lock=asyncio.Lock()
        self.running_port=state.cfg['listen_port'];self.running_host=state.cfg['listen_host']
        old=self.job()
        if old.get('phase') in BUSY:
            try:alive=old['job_id'].encode() in (Path('/proc')/str(old['worker_pid'])/'cmdline').read_bytes()
            except (OSError,KeyError):alive=False
            if not alive:atomic_json(self.file,dict(old,phase='error',message='可能是上次重启工作进程意外结束，请检查后台状态后重试'))

    def job(self):
        try:return json.loads(self.file.read_text(encoding='utf-8'))
        except (OSError,ValueError):return dict(phase='idle',message='尚未重启后台服务')

    def busy(self):return self.job().get('phase') in BUSY

    def status(self):
        return dict(supported=bool(self.state.updater.controller),running_port=self.running_port,
            saved_port=self.state.cfg['listen_port'],port_pending=self.running_port!=self.state.cfg['listen_port'],
            support_reason='' if self.state.updater.controller else '未识别到管理本项目的 systemd 或 Supervisor，需通过部署方式重启后台',job=self.job())

    async def start(self):
        async with self.lock,self.state.lock:
            if self.busy():return self.status()
            s=self.state
            if not s.updater.controller:raise ValueError(self.status()['support_reason'])
            if s.updater.busy():raise ValueError('项目正在更新，请等待完成后重启')
            await s.ensure_switch_allowed()
            if s.sessions.in_progress():raise ValueError('登录尚未完成，请先完成或取消登录')
            if s.installer.task and not s.installer.task.done():raise ValueError('本体安装或卸载尚未完成')
            if self.running_port!=s.cfg['listen_port']:
                from configure_install import check_listen_port
                check_listen_port(dict(version=1,data_dir=str(s.data.resolve()),mode='existing',updates={}),s.data)
            self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
            identifier=secrets.token_hex(12);folder=self.root/identifier;folder.mkdir(mode=0o700)
            plan=dict(job_id=identifier,job_dir=str(folder),data=str(s.data),state_file=str(self.file),
                controller=s.updater.controller,version=(SUITE_DIR/'VERSION').read_text().strip(),
                old_port=self.running_port,old_host=self.running_host,started_at=time.time())
            plan['resume_profile_id']=s.active if s.supervisor.active_port and not s.cfg.get('native_logged_out') else None
            atomic_json(folder/'plan.json',plan)
            atomic_json(self.file,dict(job_id=identifier,phase='queued',message='已提交后台重启，正在保存配置',started_at=plan['started_at']))
            try:
                await asyncio.to_thread(launch_worker,plan['controller'],sys.executable,SUITE_DIR/'restart_worker.py',folder/'plan.json',identifier,folder/'worker.log')
            except Exception:
                atomic_json(self.file,dict(job_id=identifier,phase='error',message='可能是服务管理器权限或启动工作进程异常，尚未重启后台'))
                raise ValueError('重启工作进程未启动，请检查服务日志')
            return self.status()
