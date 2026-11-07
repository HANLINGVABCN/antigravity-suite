"""Control only the service that owns this process; never accept shell commands from HTTP."""
import http.client
import os
import re
import shutil
import socket
import subprocess
import xmlrpc.client
from pathlib import Path
from urllib.parse import urlsplit


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self,path):
        super().__init__('localhost',timeout=180)
        self.path=path
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class UnixTransport(xmlrpc.client.Transport):
    def __init__(self,path):
        super().__init__()
        self.path=path
    def make_connection(self,host):
        return UnixHTTPConnection(self.path)


def detect_controller():
    if os.name!='posix':
        return None
    if os.environ.get('SUPERVISOR_ENABLED')=='1':
        address=urlsplit(os.environ.get('SUPERVISOR_SERVER_URL',''))
        group=os.environ.get('SUPERVISOR_GROUP_NAME','')
        process=os.environ.get('SUPERVISOR_PROCESS_NAME','')
        if address.scheme=='unix' and Path(address.path).is_socket() and all(re.fullmatch(r'[\w.-]+',v) for v in (group,process)):
            return dict(kind='supervisor',socket=address.path,name=group+':'+process)
    if Path('/run/systemd/system').is_dir() and shutil.which('systemctl') and shutil.which('systemd-run'):
        unit=os.environ.get('ANTIGRAVITY_SUITE_SERVICE','')
        if not unit:
            try:
                unit=next(part for part in Path('/proc/self/cgroup').read_text().split('/') if part.strip().endswith('.service')).strip()
            except (OSError,StopIteration):return None
        if re.fullmatch(r'[A-Za-z0-9_.@-]+\.service',unit):
            pid=subprocess.run(['systemctl','show',unit,'--property=MainPID','--value'],capture_output=True,text=True,timeout=5)
            if pid.returncode==0 and pid.stdout.strip()==str(os.getpid()):
                return dict(kind='systemd',name=unit)
    return None


def control(spec,action):
    if action not in ('stop','start'):
        raise ValueError('不支持的服务操作')
    if spec['kind']=='systemd':
        subprocess.run(['systemctl',action,spec['name']],check=True,capture_output=True,timeout=180)
    elif spec['kind']=='supervisor':
        server=xmlrpc.client.ServerProxy('http://localhost/RPC2',transport=UnixTransport(spec['socket']))
        getattr(server.supervisor,action+'Process')(spec['name'],True)
    else:
        raise ValueError('未识别服务管理器')


def launch_worker(controller,python,worker,plan,job_id,log):
    if controller['kind']=='systemd':
        subprocess.run(['systemd-run','--quiet','--collect','--unit=antigravity-suite-update-'+job_id,
            '--property=Type=exec','--property=UMask=0077',python,str(worker),str(plan)],
            check=True,capture_output=True,timeout=15)
    else:
        with Path(log).open('ab') as output:
            subprocess.Popen([python,str(worker),str(plan)],stdin=subprocess.DEVNULL,stdout=output,stderr=output,
                start_new_session=True,close_fds=True)
