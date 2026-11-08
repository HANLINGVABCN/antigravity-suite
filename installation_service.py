"""Local installer service selection; stdlib only, never accepts commands from HTTP."""
import argparse
import configparser
import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import time
import xmlrpc.client
from pathlib import Path

from config import atomic_json
from service_control import UnixTransport,control

NAME='antigravity-suite-improved'
RECORD='installation-service.json'


def safe_path(value):
    path=Path(value).expanduser()
    if not path.is_absolute() or any(c in str(path) for c in '\n\r\x00') or len(path.resolve().parts)<3:
        raise ValueError('安装路径需要是明确的绝对目录，不能是根目录')
    return str(path.resolve())


def read_supervisor(path):
    path=Path(path);parser=configparser.ConfigParser(interpolation=None,strict=False)
    parser.read(path,encoding='utf-8')
    address=parser.get('unix_http_server','file',fallback='').replace('%(here)s',str(path.parent))
    if not address or not Path(address).is_absolute():return None
    try:
        server=xmlrpc.client.ServerProxy('http://localhost/RPC2',transport=UnixTransport(address))
        server.supervisor.getState()
    except (OSError,xmlrpc.client.Error):return None
    includes=parser.get('include','files',fallback='').replace('%(here)s',str(path.parent))
    def decode(value):return value.replace('%%','%')
    patterns=[str(path.parent/p) if not Path(p).is_absolute() else p for p in shlex.split(includes)]
    sources=list(dict.fromkeys([path]+[Path(name) for pattern in patterns for name in sorted(glob.glob(pattern))]))
    programs=[]
    for source in sources:
        child=configparser.ConfigParser(interpolation=None,strict=False);child.read(source,encoding='utf-8')
        for section in child.sections():
            if section in ('program:'+NAME,'program:antigravity-suite'):
                command=shlex.split(decode(child.get(section,'command')))
                if len(command)<2 or Path(command[1]).name!='server.py':
                    raise ValueError('同名 Supervisor 服务不是本项目，拒绝覆盖')
                root=safe_path(Path(command[1]).parent)
                env=decode(child.get(section,'environment',fallback=''))
                match=re.search(r'(?:^|,)\s*ANTIGRAVITY_SUITE_DATA="([^"]+)"',env)
                data=safe_path(match.group(1).replace('\\"','"').replace('\\\\','\\')) if match else str(Path(root)/'data')
                programs.append(dict(name=section.split(':',1)[1],program_file=str(source),install_dir=root,data_dir=data))
    if len(programs)>1:raise ValueError('发现多个本项目 Supervisor 服务，请指定实际 Supervisor 配置文件')
    if programs:
        item=programs[0]
        return dict(controller=dict(kind='supervisor',socket=address,name=item['name']),supervisor_config=str(path),owned_supervisor=False,**item)
    for pattern in patterns:
        file=Path(pattern)
        if file.name=='*.conf' and '*' not in str(file.parent):
            return dict(controller=dict(kind='supervisor',socket=address,name=NAME),supervisor_config=str(path),program_file=str(file.parent/(NAME+'.conf')),owned_supervisor=False)
    return None


def prepare(source,install_dir=None,data_dir=None,manager='auto',supervisor_config=None):
    source=Path(source).resolve();found=None
    if manager not in ('auto','systemd','supervisor'):raise ValueError('服务管理器选项无效')
    if manager=='systemd' or (manager=='auto' and not supervisor_config and Path('/run/systemd/system').is_dir() and shutil.which('systemctl')):
        if not Path('/run/systemd/system').is_dir():raise ValueError('当前环境没有可运行的 systemd')
        found=dict(controller=dict(kind='systemd',name=NAME+'.service'),owned_supervisor=False)
        unit=Path('/etc/systemd/system')/(NAME+'.service')
        if unit.is_file():
            text=unit.read_text(encoding='utf-8')
            match=re.search(r'^ExecStart=(.+)$',text,re.M)
            command=shlex.split(match.group(1).replace('%%','%')) if match else []
            if len(command)<2 or Path(command[1]).name!='server.py':raise ValueError('同名 systemd 服务不是本项目，拒绝覆盖')
            found['install_dir']=safe_path(Path(command[1]).parent)
            for line in re.findall(r'^Environment=(.+)$',text,re.M):
                for value in shlex.split(line.replace('%%','%')):
                    if value.startswith('ANTIGRAVITY_SUITE_DATA='):found['data_dir']=safe_path(value.split('=',1)[1])
    else:
        candidates=[supervisor_config] if supervisor_config else [
            '/config/keepalive/supervisord.conf','/etc/supervisor/supervisord.conf','/etc/supervisord.conf']
        default_data=Path(install_dir or '/config/antigravity-suite-improved')/'data' if Path('/config').is_dir() else Path('/var/lib/antigravity-suite-improved')
        preferred=Path(data_dir or default_data)/RECORD
        previous={}
        if preferred and preferred.is_file():
            previous=json.loads(preferred.read_text(encoding='utf-8'))
            if previous.get('supervisor_config') and not supervisor_config:candidates.insert(0,previous['supervisor_config'])
        for candidate in dict.fromkeys(candidates):
            if candidate and Path(candidate).is_file():
                found=read_supervisor(candidate)
                if found:
                    if found['supervisor_config']==previous.get('supervisor_config'):
                        found.update({key:previous[key] for key in ('owned_supervisor','boot_script','boot_link') if key in previous})
                    break
        if supervisor_config and not found:raise ValueError('指定的 Supervisor 尚未运行或没有可用的本机控制接口')
    container=not found or found['controller']['kind']=='supervisor'
    default_install='/config/antigravity-suite-improved' if container and Path('/config').is_dir() else '/opt/antigravity-suite-improved'
    install=safe_path(install_dir or (found or {}).get('install_dir') or default_install)
    data=safe_path(data_dir or (found or {}).get('data_dir') or (str(Path(install)/'data') if container and Path('/config').is_dir() else '/var/lib/antigravity-suite-improved'))
    record=Path(data)/RECORD
    if not found and record.is_file():
        previous=json.loads(record.read_text(encoding='utf-8'))
        if previous.get('supervisor_config'):
            existing=read_supervisor(previous['supervisor_config'])
            if existing:found=dict(existing,owned_supervisor=previous.get('owned_supervisor',False))
    if found and found.get('install_dir') and (install!=found['install_dir'] or data!=found['data_dir']):
        raise ValueError('已有服务目录与指定目录不一致，请使用现有目录升级，避免创建重复后台')
    if Path(install)==Path(data) or Path(install).is_relative_to(Path(data)):
        raise ValueError('源码目录不能位于私有数据目录内')
    if not found:
        folder=Path(data)/'supervisor';label=hashlib.sha256(data.encode()).hexdigest()[:12]
        socket='/run/antigravity-suite-'+label+'.sock'
        found=dict(controller=dict(kind='supervisor',socket=socket,name=NAME),owned_supervisor=True,
            supervisor_config=str(folder/'supervisord.conf'),program_file=str(folder/'program.conf'))
        if Path(found['supervisor_config']).is_file():
            running=read_supervisor(found['supervisor_config'])
            if running:found=dict(running,owned_supervisor=True)
    found.update(source=str(source),install_dir=install,data_dir=data,was_active=False)
    return found


def server(plan):
    return xmlrpc.client.ServerProxy('http://localhost/RPC2',transport=UnixTransport(plan['controller']['socket']))


def running(plan):
    spec=plan['controller']
    if spec['kind']=='systemd':
        return subprocess.run(['systemctl','is-active','--quiet',spec['name']],capture_output=True).returncode==0
    try:return server(plan).supervisor.getProcessInfo(spec['name'])['statename']=='RUNNING'
    except (OSError,xmlrpc.client.Error):return False


def stop(plan):
    plan['was_active']=running(plan)
    if plan['controller']['kind']=='supervisor':
        try:plan['was_active']=server(plan).supervisor.getProcessInfo(plan['controller']['name'])['statename'] in ('RUNNING','STARTING','BACKOFF')
        except (OSError,xmlrpc.client.Error):pass
    if plan['was_active']:control(plan['controller'],'stop')


def quoted(value):return shlex.quote(str(value)).replace('%','%%')
def supervisor_environment(value):return str(value).replace('%','%%').replace('\\','\\\\').replace('"','\\"')


def program_text(plan):
    root=Path(plan['install_dir']);data=Path(plan['data_dir']);name=plan['controller']['name'].split(':')[0]
    return ('[program:'+name+']\ncommand='+quoted(root/'.venv/bin/python')+' '+quoted(root/'server.py')+
        '\ndirectory='+str(root).replace('%','%%')+'\nenvironment=ANTIGRAVITY_SUITE_DATA="'+supervisor_environment(data)+'",PYTHONUNBUFFERED="1"\n'+
        'autostart=true\nautorestart=true\nstartsecs=3\nstartretries=5\nstopsignal=TERM\nstopwaitsecs=70\nstopasgroup=false\nkillasgroup=true\numask=0077\n'+
        'stdout_logfile='+str(data/'suite.log').replace('%','%%')+'\nstdout_logfile_maxbytes=10MB\nstdout_logfile_backups=3\nredirect_stderr=true\n')


def container_init_directory():
    file=Path('/etc/s6-overlay/s6-rc.d/init-custom-files/run')
    if file.is_file() and '/custom-cont-init.d' in file.read_text(encoding='utf-8'):return Path('/custom-cont-init.d')
    return None


def atomic_text(path,text,mode=0o600):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name('.'+path.name+'.install-'+str(os.getpid()))
    try:temp.write_text(text,encoding='utf-8');temp.chmod(mode);os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)


def update_section(path,section,body):
    original=Path(path).read_text(encoding='utf-8') if Path(path).is_file() else ''
    pattern=re.compile(r'^\['+re.escape(section)+r'\]\s*\n.*?(?=^\[|\Z)',re.M|re.S)
    if pattern.search(original):text=pattern.sub(lambda _:body,original,count=1)
    else:text=original.rstrip()+'\n\n'+body if original else body
    atomic_text(path,text)


def register(plan):
    root=Path(plan['install_dir']);data=Path(plan['data_dir']);spec=plan['controller']
    if spec['kind']=='systemd':
        file=Path('/etc/systemd/system')/spec['name']
        plan.setdefault('service_original',{})[str(file)]=file.read_text(encoding='utf-8') if file.is_file() else None
        text='[Unit]\nDescription=Antigravity Suite Improved\nAfter=network.target\n[Service]\nType=simple\nUser=root\nWorkingDirectory='+str(root).replace('%','%%')+'\nEnvironment='+json.dumps('ANTIGRAVITY_SUITE_DATA='+str(data),ensure_ascii=False).replace('%','%%')+'\nEnvironment=PYTHONUNBUFFERED=1\nEnvironment=ANTIGRAVITY_SUITE_SERVICE='+spec['name']+'\nUMask=0077\nExecStart='+quoted(root/'.venv/bin/python')+' '+quoted(root/'server.py')+'\nRestart=on-failure\nRestartSec=5\nTimeoutStopSec=70\nKillMode=mixed\n[Install]\nWantedBy=multi-user.target\n'
        atomic_text(Path('/etc/systemd/system')/spec['name'],text)
        subprocess.run(['systemctl','daemon-reload'],check=True)
        subprocess.run(['systemctl','enable',spec['name']],check=True,capture_output=True)
    else:
        file=Path(plan['program_file'])
        plan.setdefault('service_original',{})[str(file)]=file.read_text(encoding='utf-8') if file.is_file() else None
        update_section(plan['program_file'],'program:'+spec['name'].split(':')[0],program_text(plan))
        if plan['owned_supervisor']:
            folder=Path(plan['supervisor_config']).parent
            file=Path(plan['supervisor_config'])
            plan['service_original'][str(file)]=file.read_text(encoding='utf-8') if file.is_file() else None
            text='[unix_http_server]\nfile='+spec['socket']+'\nchmod=0600\n[supervisord]\npidfile='+str(folder/'supervisord.pid')+'\nlogfile='+str(folder/'supervisord.log')+'\nlogfile_maxbytes=5MB\nlogfile_backups=2\nuser=root\numask=0077\n[rpcinterface:supervisor]\nsupervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface\n[supervisorctl]\nserverurl=unix://'+spec['socket']+'\n[include]\nfiles='+plan['program_file']+'\n'
            atomic_text(plan['supervisor_config'],text)
            boot=folder/'start.sh'
            command=shutil.which('supervisord')
            if not command:raise ValueError('需要安装 supervisor 运行依赖')
            atomic_text(boot,'#!/bin/sh\n# antigravity-suite-owned-supervisor\n'+shlex.quote(shutil.which('supervisorctl'))+' -c '+shlex.quote(plan['supervisor_config'])+' pid >/dev/null 2>&1 && exit 0\nexec '+shlex.quote(command)+' -c '+shlex.quote(plan['supervisor_config'])+'\n',0o700)
            plan['boot_script']=str(boot)
            # LinuxServer/s6's verified custom init runner also executes symlinked scripts.
            hook=container_init_directory()
            if hook:
                bootstrap=hook/('antigravity-suite-'+hashlib.sha256(str(data).encode()).hexdigest()[:12]+'.sh')
                bootstrap.parent.mkdir(parents=True,exist_ok=True)
                if bootstrap.exists() or bootstrap.is_symlink():
                    if not bootstrap.is_symlink() or bootstrap.resolve()!=boot.resolve():raise ValueError('同名容器启动脚本已存在，拒绝覆盖')
                else:bootstrap.symlink_to(boot)
                plan['boot_link']=str(bootstrap)
    recorded={key:value for key,value in plan.items() if key not in ('service_original','source','was_active')}
    atomic_json(data/RECORD,recorded)


def start(plan):
    spec=plan['controller']
    if spec['kind']=='systemd':control(spec,'start');return
    if plan['owned_supervisor']:
        subprocess.run(['sh',plan.get('boot_script',str(Path(plan['supervisor_config']).parent/'start.sh'))],check=True,capture_output=True,timeout=15)
    rpc=server(plan)
    added,changed,removed=rpc.supervisor.reloadConfig()[0]
    name=spec['name'].split(':')[0]
    if name in changed:
        try:rpc.supervisor.stopProcessGroup(name,True)
        except xmlrpc.client.Fault as error:
            if error.faultCode not in (10,70):raise
        rpc.supervisor.removeProcessGroup(name);rpc.supervisor.addProcessGroup(name)
    elif name in added:rpc.supervisor.addProcessGroup(name)
    state=rpc.supervisor.getProcessInfo(spec['name'])['statename']
    if state not in ('RUNNING','STARTING'):control(spec,'start')


def recover(plan):
    spec=plan['controller']
    if running(plan):control(spec,'stop')
    for path,text in plan.get('service_original',{}).items():
        if text is None:Path(path).unlink(missing_ok=True)
        else:atomic_text(path,text)
    if spec['kind']=='systemd':subprocess.run(['systemctl','daemon-reload'],check=True)
    elif not plan['was_active']:
        try:
            rpc=server(plan);name=spec['name'].split(':')[0]
            rpc.supervisor.removeProcessGroup(name)
            if plan['owned_supervisor'] and not rpc.supervisor.getAllProcessInfo():rpc.supervisor.shutdown()
        except (OSError,xmlrpc.client.Error):pass
        link=Path(plan.get('boot_link','/nonexistent-suite-boot'))
        if link.is_symlink() and link.resolve()==Path(plan.get('boot_script','/nonexistent')).resolve():link.unlink()
    if plan['was_active']:start(plan)


def remove(plan):
    spec=plan['controller'];stop(plan)
    if spec['kind']=='systemd':
        subprocess.run(['systemctl','disable',spec['name']],capture_output=True,check=False)
        (Path('/etc/systemd/system')/spec['name']).unlink(missing_ok=True)
        subprocess.run(['systemctl','daemon-reload'],check=True)
    else:
        name=spec['name'].split(':')[0]
        try:server(plan).supervisor.removeProcessGroup(name)
        except xmlrpc.client.Fault as error:
            if error.faultCode!=10:raise
        update_section(plan['program_file'],'program:'+name,'')
        if plan['owned_supervisor']:
            rpc=server(plan)
            if not rpc.supervisor.getAllProcessInfo():rpc.supervisor.shutdown()
            link=Path(plan.get('boot_link','/nonexistent-suite-boot'))
            if link.is_symlink() and link.resolve()==Path(plan.get('boot_script','/nonexistent')).resolve():link.unlink()
    (Path(plan['data_dir'])/RECORD).unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description='安装服务管理器自动选择')
    parser.add_argument('action',choices=('prepare','stop','register','start','running','restore','remove'))
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--source',type=Path);parser.add_argument('--install-dir');parser.add_argument('--data-dir')
    parser.add_argument('--manager',default='auto');parser.add_argument('--supervisor-config')
    args=parser.parse_args()
    try:
        if args.action=='prepare':
            plan=prepare(args.source,args.install_dir,args.data_dir,args.manager,args.supervisor_config)
            atomic_json(args.plan,plan)
            print('服务方式：'+('systemd' if plan['controller']['kind']=='systemd' else '复用已有 Supervisor' if not plan['owned_supervisor'] else '容器独立 Supervisor'))
            print('源码目录：'+plan['install_dir']);print('数据目录：'+plan['data_dir']);return 0
        plan=json.loads(args.plan.read_text(encoding='utf-8'))
        if args.action=='running':return 0 if running(plan) else 1
        if args.action=='stop':stop(plan);atomic_json(args.plan,plan)
        if args.action=='register':
            try:register(plan)
            finally:atomic_json(args.plan,plan)
        if args.action=='start':start(plan)
        if args.action=='restore':recover(plan)
        if args.action=='remove':remove(plan)
        return 0
    except (ValueError,OSError,configparser.Error,subprocess.SubprocessError,xmlrpc.client.Error) as error:
        print('服务配置失败：'+str(error),file=__import__('sys').stderr);return 1


if __name__=='__main__':raise SystemExit(main())
