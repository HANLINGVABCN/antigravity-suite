"""Detached management restart; fixed controller only, no HTTP-provided commands."""
import json
import os
import sys
import time
from pathlib import Path

from service_control import control
from update_worker import atomic,check_running,security_snapshot


def run_restart(plan,service=control,health=check_running):
    os.umask(0o077)
    def report(phase,message,**fields):
        atomic(plan['state_file'],dict(job_id=plan['job_id'],phase=phase,message=message,started_at=plan['started_at'],worker_pid=os.getpid(),**fields))
    stopped=False
    try:
        time.sleep(1)  # Allow the accepted HTTP response to reach the requesting page.
        report('stopping','正在保存运行配置并停止后台')
        service(plan['controller'],'stop');stopped=True
        saved=security_snapshot(plan['data'])
        if plan.get('resume_profile_id'):
            file=Path(plan['data'])/'config.json';cfg=json.loads(file.read_text(encoding='utf-8'))
            cfg['restart_resume_profile_id']=plan['resume_profile_id'];atomic(file,cfg)
        report('starting','正在启动后台服务，等待连接恢复')
        service(plan['controller'],'start');stopped=False
        health(plan,plan['version'])
        if security_snapshot(plan['data'])!=saved:raise ValueError('设置保留检查失败')
        report('success','后台服务已重启，已保存的端口设置生效')
    except Exception:
        restored=False
        try:
            report('recovering','重启未通过检查，尝试恢复原监听端口')
            if not stopped:service(plan['controller'],'stop')
            file=Path(plan['data'])/'config.json';cfg=json.loads(file.read_text(encoding='utf-8'))
            cfg.update(listen_host=plan['old_host'],listen_port=plan['old_port']);atomic(file,cfg)
            service(plan['controller'],'start');health(plan,plan['version']);restored=True
        except Exception:pass
        report('error','可能是端口占用、后台启动或服务管理器异常；'+('已恢复原端口和后台。' if restored else '请检查管理服务日志和保存的数据。'),restored=restored)


if __name__=='__main__':
    try:
        path=Path(sys.argv[1]).resolve();plan=json.loads(path.read_text(encoding='utf-8'))
        expected=(Path(plan['data'])/'service-restarts'/plan['job_id']/'plan.json').resolve()
        if path!=expected:raise ValueError('重启工作计划路径无效')
        run_restart(plan)
    except Exception:raise SystemExit('后台重启工作进程异常，请检查私有日志')
