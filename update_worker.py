"""Detached, standard-library update worker; survives the management service restart."""
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from service_control import control


def atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,temp=tempfile.mkstemp(dir=path.parent,prefix='.update-')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as output:
            json.dump(value,output,ensure_ascii=False,indent=2);output.flush();os.fsync(output.fileno())
        os.replace(temp,path);os.chmod(path,0o600)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def progress(plan,phase,message,**fields):
    state=dict(job_id=plan['job_id'],phase=phase,message=message,target_version=plan['version'],
               started_at=plan['started_at'],updated_at=time.time(),worker_pid=os.getpid(),**fields)
    atomic(plan['state_file'],state)


def security_snapshot(data):
    cfg=json.loads((Path(data)/'config.json').read_text(encoding='utf-8'))
    keys=('auth_user','auth_password_hash','auth_pass','session_secret','mfa_enabled','mfa_secret','mfa_recovery_hashes')
    profiles=json.loads((Path(data)/'profiles.json').read_text(encoding='utf-8')) if (Path(data)/'profiles.json').is_file() else []
    nodes=Path(data)/'proxies.json'
    return dict(auth={k:cfg.get(k) for k in keys},
        profiles=sorted((p['id'],p.get('proxy_id'),p.get('created_at')) for p in profiles),
        proxies=hashlib.sha256(nodes.read_bytes()).hexdigest() if nodes.is_file() else None,
        host=cfg['listen_host'],port=cfg['listen_port'],default=cfg.get('default_profile_id'))


def check_running(plan,expected):
    cfg=json.loads((Path(plan['data'])/'config.json').read_text(encoding='utf-8'))
    host=cfg['listen_host'];host='127.0.0.1' if host=='0.0.0.0' else '::1' if host=='::' else host
    if ':' in host:host='['+host+']'
    text=cfg['auth_user']+'.'+str(int(time.time()))
    cookie=text+'.'+hmac.new(cfg['session_secret'].encode(),text.encode(),hashlib.sha256).hexdigest()
    url='http://%s:%s/__suite/api/status'%(host,cfg['listen_port'])
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline=time.monotonic()+100
    while time.monotonic()<deadline:
        try:
            request=urllib.request.Request(url,headers={'Cookie':'ag_session='+cookie})
            with opener.open(request,timeout=3) as response:
                status=json.loads(response.read(1024*1024))
            if status.get('suite_version')==expected:
                return
        except (OSError,ValueError,urllib.error.URLError):pass
        time.sleep(1)
    raise RuntimeError('更新后后台未通过启动检查')


def replace_file(source,target):
    target.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
    if target.is_symlink():raise ValueError('源码目标不能是符号链接')
    fd,temp=tempfile.mkstemp(dir=target.parent,prefix='.suite-update-')
    try:
        with os.fdopen(fd,'wb') as output:
            with source.open('rb') as incoming:shutil.copyfileobj(incoming,output)
            output.flush();os.fsync(output.fileno())
        os.chmod(temp,target.stat().st_mode & 0o777 if target.exists() else 0o644)
        os.replace(temp,target)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def build_environment(plan,runner=subprocess.run):
    environment=Path(plan['job_dir'])/'environment'
    base=getattr(sys,'_base_executable',sys.executable)
    try:
        runner([base,'-m','venv',str(environment)],check=True,capture_output=True,timeout=120)
    except subprocess.CalledProcessError:
        # Some supported installations were bootstrapped with virtualenv instead of ensurepip.
        original=Path(plan['live'])/'.venv'
        if not original.is_dir():raise
        if environment.exists():shutil.rmtree(environment)
        shutil.copytree(original,environment,symlinks=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        for path in environment.rglob('*'):
            if path.is_symlink():
                target=Path(os.readlink(path))
                if target.is_absolute() and target.is_relative_to(original):
                    path.unlink();path.symlink_to(environment/target.relative_to(original))
    python=environment/'bin/python'
    runner([str(python),'-c','import sys; assert sys.prefix == '+repr(str(environment))],check=True,capture_output=True,timeout=15)
    runner([str(python),'-m','pip','install','--disable-pip-version-check','--timeout','30','--retries','2',
            '-r',str(Path(plan['source'])/'requirements.txt')],check=True,capture_output=True,timeout=600)
    runner([str(python),'-c','import aiohttp,aiohttp_socks,qrcode'],check=True,capture_output=True,timeout=20)
    # Entry-point scripts should point at the final installed environment.
    for path in (environment/'bin').iterdir():
        if path.is_file() and not path.is_symlink():
            data=path.read_bytes()
            prefix=('#!'+str(environment)).encode()
            if data.startswith(prefix):path.write_bytes(data.replace(prefix,('#!'+str(Path(plan['live'])/'.venv')).encode(),1))
    return environment


def run_update(plan,service=control,health=check_running,build=build_environment):
    os.umask(0o077)
    live=Path(plan['live']).resolve();source=Path(plan['source']).resolve();job=Path(plan['job_dir']).resolve()
    backup=job/'backup';backup.mkdir(mode=0o700,exist_ok=True)
    old_version=(live/'VERSION').read_text().strip()
    source_manifest=plan['files']
    for name,expected in source_manifest.items():
        path=(source/name).resolve();path.relative_to(source)
        target=(live/name).resolve();target.relative_to(live)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError('下载后的源码校验失败')
    managed_path=Path(plan['data'])/'updates/installed-files.json'
    prior=json.loads(managed_path.read_text()) if managed_path.is_file() else []
    names=sorted(set(source_manifest)|set(prior))
    from pathlib import PurePosixPath
    for name in names:
        path=PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name or not path.parts:
            raise ValueError('已安装源码清单包含不安全路径')
        if set(path.parts)&{'data','profiles','.venv','.git','updates','config.json','profiles.json','proxies.json'}:
            raise ValueError('源码清单不能修改私有数据')
    stopped=False;changed=[];environment_swapped=False;had_environment=(live/'.venv').exists()
    try:
        progress(plan,'dependencies','正在准备新版运行环境，原服务继续运行')
        environment=build(plan)
        progress(plan,'backup','正在保存运行状态并备份，后台将短暂重启')
        service(plan['controller'],'stop');stopped=True
        saved=security_snapshot(plan['data'])
        for name in names:
            target=live/name
            if target.is_file():
                (backup/'source'/name).parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(target,backup/'source'/name)
        # Includes the original configuration and chat state, avoids recursively backing up this job.
        with tarfile.open(backup/'private-data.tar.gz','w:gz') as archive:
            for entry in Path(plan['data']).iterdir():
                if entry.name!='updates':archive.add(entry,arcname=entry.name)
        for name in ('config.json','profiles.json','proxies.json','shared-native-runtime.json'):
            path=Path(plan['data'])/name
            if path.is_file():shutil.copy2(path,backup/name)
        progress(plan,'installing','正在替换管理后台，账号和节点保留',backup_dir=str(backup))
        for name in names:
            changed.append(name);target=live/name
            if name in source_manifest:replace_file(source/name,target)
            elif target.is_file():target.unlink()
        if had_environment:os.replace(live/'.venv',job/'previous-environment')
        try:os.replace(environment,live/'.venv')
        except Exception:
            if had_environment:os.replace(job/'previous-environment',live/'.venv')
            raise
        environment_swapped=True
        progress(plan,'restarting','新版后台正在启动，请稍候',backup_dir=str(backup))
        service(plan['controller'],'start');stopped=False
        health(plan,plan['version'])
        if security_snapshot(plan['data'])!=saved:raise RuntimeError('账号、节点或监听设置的保留检查未通过')
        atomic(managed_path,sorted(source_manifest))
        progress(plan,'success','更新完成，账号配置已保留',backup_dir=str(backup),current_version=plan['version'])
    except Exception:
        if changed:
            progress(plan,'rolling_back','新版未通过检查，正在恢复原版本',backup_dir=str(backup))
            if not stopped:
                try:service(plan['controller'],'stop')
                except Exception:pass
                stopped=True
            for name in reversed(changed):
                old=backup/'source'/name;target=live/name
                if old.is_file():replace_file(old,target)
                elif target.is_file():target.unlink()
            if environment_swapped:
                os.replace(live/'.venv',job/'failed-environment')
                if had_environment:os.replace(job/'previous-environment',live/'.venv')
            for name in ('config.json','profiles.json','proxies.json','shared-native-runtime.json'):
                if (backup/name).is_file():replace_file(backup/name,Path(plan['data'])/name)
        restored=False
        if stopped:
            try:
                service(plan['controller'],'start');health(plan,old_version);restored=True
            except Exception:pass
        progress(plan,'error','可能是下载、依赖、磁盘空间或新版启动存在问题。'+
            ('已恢复原版本。' if restored else '原服务未被修改。' if not changed and not stopped else '自动恢复未完成，请检查服务器服务日志。'),
            rollback_succeeded=restored,backup_dir=str(backup),current_version=old_version)
    finally:
        lock=Path(plan['data'])/'updates/update.lock'
        if lock.is_file() and lock.read_text().strip()==plan['job_id']:lock.unlink()


if __name__=='__main__':
    plan=None
    try:
        plan=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
        run_update(plan)
    except Exception:
        if plan:
            progress(plan,'error','可能是更新任务校验或恢复过程异常，请检查服务状态和私有备份')
            lock=Path(plan['data'])/'updates/update.lock'
            if lock.is_file() and lock.read_text().strip()==plan['job_id']:lock.unlink()
        # Keep credentials, command output and local paths out of public error messages.
        raise SystemExit('更新工作进程异常，请查看私有更新目录和服务日志')
