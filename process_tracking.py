"""Identify and stop only the exact engine and descendants being managed."""
import os
import signal
import subprocess
import time
import json
from pathlib import Path

from discovery import process_info


class ExistingProcess:
    def __init__(self,info):self.pid=info['pid'];self.identity=info['start_time']
    def poll(self):
        value=process_info(self.pid)
        return None if value and value['start_time']==self.identity and value['state']!='Z' else 0
    def wait(self,timeout=None):
        deadline=time.monotonic()+(timeout if timeout is not None else 60)
        while self.poll() is None:
            if time.monotonic()>=deadline:raise subprocess.TimeoutExpired('adopted Antigravity',timeout)
            time.sleep(.1)
        return 0


def descendants(pid):
    all_processes={}
    for entry in Path('/proc').iterdir() if Path('/proc').is_dir() else []:
        if entry.name.isdigit():
            info=process_info(entry.name)
            if info:all_processes[info['pid']]=info
    result=[];parents={pid}
    while True:
        found=[p for p in all_processes.values() if p['ppid'] in parents and p['pid'] not in parents]
        if not found:break
        result.extend(found);parents.update(p['pid'] for p in found)
    return result


def stop_process(process):
    if process is None or process.poll() is not None:return
    main=process_info(process.pid)
    if not main:return
    children=descendants(process.pid)
    def send(info,signal_number):
        current=process_info(info['pid'])
        if current and current['start_time']==info['start_time']:
            try:os.kill(info['pid'],signal_number)
            except ProcessLookupError:pass
    if os.getpgid(process.pid)==process.pid:
        try:os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:pass
    else:
        # Never kill a desktop session's shared process group.
        send(main,signal.SIGTERM)
    try:process.wait(timeout=6)
    except subprocess.TimeoutExpired:send(main,signal.SIGKILL);process.wait(timeout=3)
    for child in reversed(children):send(child,signal.SIGTERM)
    deadline=time.monotonic()+2
    while time.monotonic()<deadline:
        if not any((p:=process_info(child['pid'])) and p['start_time']==child['start_time'] and p['state']!='Z' for child in children):break
        time.sleep(.1)
    for child in reversed(children):send(child,signal.SIGKILL)


def process_ports(pid):
    identities=[pid]+[p['pid'] for p in descendants(pid)]
    inodes=set()
    for identifier in identities:
        folder=Path('/proc')/str(identifier)/'fd'
        try:
            for file in folder.iterdir():
                try:
                    link=os.readlink(file)
                    if link.startswith('socket:['):inodes.add(link[8:-1])
                except OSError:pass
        except OSError:pass
    if not inodes and os.geteuid()==0:
        info=process_info(pid)
        if info and info['uid']!=0:
            import pwd
            account=pwd.getpwuid(info['uid'])
            source="import os,json;from pathlib import Path; ids="+repr(identities)+"; result=[]\nfor pid in ids:\n try:\n  for file in (Path('/proc')/str(pid)/'fd').iterdir():\n   try:\n    link=os.readlink(file)\n    if link.startswith('socket:['):result.append(link[8:-1])\n   except OSError:pass\n except OSError:pass\nprint(json.dumps(result))"
            result=subprocess.run(['/usr/bin/python3','-c',source],user=account.pw_uid,group=account.pw_gid,extra_groups=[],capture_output=True,text=True,timeout=5)
            if result.returncode==0:
                try:inodes.update(json.loads(result.stdout))
                except ValueError:pass
    ports=set()
    for file in [Path('/proc/net/tcp'),Path('/proc/net/tcp6')]:
        try:lines=file.read_text().splitlines()[1:]
        except OSError:continue
        for line in lines:
            parts=line.split()
            if len(parts)>9 and parts[3]=='0A' and parts[9] in inodes:
                ports.add(int(parts[1].split(':')[-1],16))
    return ports
