"""Find usable Antigravity installations and their existing data directories."""
import json
import os
import platform
import shutil
import struct
import subprocess
from pathlib import Path

SESSION_KEYS=('DISPLAY','XAUTHORITY','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS','WAYLAND_DISPLAY')


def process_info(pid,proc_root=Path('/proc')):
    path=Path(proc_root)/str(pid)
    try:
        stat=(path/'stat').read_text();tail=stat[stat.rfind(')')+2:].split()
        status=dict(line.split(':',1) for line in (path/'status').read_text().splitlines() if ':' in line)
        raw=(path/'cmdline').read_bytes().decode(errors='replace')
        args=raw.split('\0');uid=int(status['Uid'].split()[0])
        try:env=dict(pair.split('=',1) for pair in (path/'environ').read_bytes().decode(errors='replace').split('\0') if '=' in pair)
        except OSError:
            import pwd
            env={'HOME':pwd.getpwuid(uid).pw_dir}
        try:executable=str(Path(os.readlink(path/'exe')).resolve())
        except OSError:
            # Some desktop containers deny /proc/exe and environ across users.
            # Use the readable command identity and the OS account's HOME instead.
            executable=str(Path(args[0].split(' --',1)[0]).resolve())
        if not Path(executable).is_file():return None
        return dict(pid=int(pid),ppid=int(tail[1]),start_time=tail[19],state=tail[0],uid=uid,
            executable=executable,args=args,raw=raw,env=env)
    except (OSError,ValueError,KeyError,IndexError):return None


def engine_processes(proc_root=Path('/proc')):
    result=[]
    if not Path(proc_root).is_dir():return result
    for path in Path(proc_root).iterdir():
        if not path.name.isdigit():continue
        item=process_info(path.name,proc_root)
        if not item or Path(item['executable']).name!='antigravity' or '--type=' in item['raw']:continue
        result.append(item)
    return result


def config_dir_for(item):
    args=item['args']
    for index,arg in enumerate(args):
        if arg.startswith('--user-data-dir='):return Path(arg.split('=',1)[1]).resolve()
        if arg=='--user-data-dir' and index+1<len(args):return Path(args[index+1]).resolve()
    home=Path(item['env'].get('HOME','/root'))
    return (Path(item['env'].get('XDG_CONFIG_HOME',str(home/'.config')))/'Antigravity').resolve()


def validate_binary(path,check_libraries=True):
    path=Path(path)
    report=dict(path=str(path),usable=False,reason='没有可执行的反重力本体')
    if not path.is_file() or not os.access(path,os.X_OK):return report
    resolved=path.resolve();report['path']=str(resolved)
    if not (resolved.parent/'resources/app.asar').is_file():
        report['reason']='缺少本体资源文件';return report
    try:
        with resolved.open('rb') as file:header=file.read(20)
        if len(header)<20 or header[:4]!=b'\x7fELF':report['reason']='本体不是可用的 Linux 可执行文件';return report
        machine=struct.unpack('<H' if header[5]==1 else '>H',header[18:20])[0]
        expected={'x86_64':62,'amd64':62,'aarch64':183,'arm64':183}.get(platform.machine().lower())
        if expected is not None and machine!=expected:report['reason']='本体架构与服务器不匹配';return report
        if check_libraries and shutil.which('ldd'):
            result=subprocess.run(['ldd',str(resolved)],capture_output=True,text=True,timeout=8)
            if 'not found' in result.stdout:report['reason']='本体运行库不完整';return report
        report.update(usable=True,reason='发现可执行本体、资源和匹配的运行架构')
    except (OSError,subprocess.TimeoutExpired):report['reason']='无法验证本体可用性'
    return report


def discover(cfg,proc_root=Path('/proc'),home_candidates=None,binary_candidates=None):
    engines=engine_processes(proc_root)
    data=Path(cfg.get('data_dir',Path(cfg['runtime_home']).parent)).resolve()
    managed=Path(cfg.get('core_directory',data/'core')).resolve()
    candidates=[Path(p['executable']) for p in engines]
    candidates+=([Path(p) for p in binary_candidates] if binary_candidates is not None else
        [Path(cfg['antigravity_bin']),Path('/opt/Antigravity/antigravity'),Path('/opt/antigravity/antigravity'),Path('/usr/share/antigravity/antigravity')])
    command=shutil.which('antigravity')
    if command:candidates.append(Path(command))
    reports=[];seen=set()
    for path in candidates:
        key=str(path.resolve())
        if key in seen:continue
        seen.add(key);reports.append(validate_binary(path))
    usable=[r for r in reports if r['usable']]
    usable.sort(key=lambda r:(Path(r['path']).is_relative_to(managed),r['path']!=str(Path(cfg['antigravity_bin']).resolve())))
    selected=usable[0]['path'] if usable else None
    homes=[]
    for process in engines:
        if process['env'].get('HOME'):homes.append((Path(process['env']['HOME']),config_dir_for(process),process))
    if home_candidates is None:
        defaults=[Path.home(),Path('/config')]
        if Path('/home').is_dir():defaults+=list(Path('/home').iterdir())
        try:
            import pwd
            defaults+=[Path(u.pw_dir) for u in pwd.getpwall() if u.pw_uid==0 or 1000<=u.pw_uid<65534]
        except ImportError:pass
    else:defaults=[Path(p) for p in home_candidates]
    homes += [(home,home/'.config/Antigravity',None) for home in defaults]
    results=[];seen=set()
    for home,config,process in homes:
        home=home.resolve();config=config.resolve();key=(str(home),str(config))
        if key in seen or home.is_relative_to(data) or config.is_relative_to(data):continue
        seen.add(key)
        token=home/'.gemini/jetski-standalone-oauth-token'
        if not home.is_dir() or not (config.is_dir() or token.is_file()):continue
        uid=process['uid'] if process else home.stat().st_uid
        env={k:v for k,v in (process['env'] if process else os.environ).items() if k in SESSION_KEYS}
        if not process:
            # Discover the matching user's display environment without taking credentials.
            for entry in Path(proc_root).iterdir() if Path(proc_root).is_dir() else []:
                if not entry.name.isdigit():continue
                candidate=process_info(entry.name,proc_root)
                if candidate and candidate['uid']==uid and candidate['env'].get('HOME')==str(home) and candidate['env'].get('DISPLAY'):
                    env={k:v for k,v in candidate['env'].items() if k in SESSION_KEYS};break
        if not env.get('DISPLAY') and Path('/tmp/.X11-unix/X1').exists():env['DISPLAY']=':1'
        effective_uid=os.geteuid() if hasattr(os,'geteuid') else -1
        can_manage=effective_uid==0 or effective_uid==uid
        binary=process['executable'] if process and any(r['path']==process['executable'] and r['usable'] for r in reports) else selected
        results.append(dict(home=str(home),config_dir=str(config),uid=uid,binary=binary,display=env.get('DISPLAY',cfg['display_num']),
            session_env=env,available=bool(binary and can_manage),pid=process['pid'] if process else None))
    return dict(binary=selected,installations=reports,configurations=results)
