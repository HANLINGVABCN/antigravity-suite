"""Stop only CLI processes owned by the user and HOME being switched."""
import errno
import json
import os
import signal
import sys
import time
from pathlib import Path


def process_identity(process, home, uid, binary):
    try:
        if process.stat().st_uid != uid:
            return None
        fields = (process / 'stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] in ('Z', 'X'):
            return None
        executable = os.readlink(process / 'exe').removesuffix(' (deleted)')
        if Path(executable).resolve() != binary:
            return None
        env = dict(part.split('=', 1) for part in (process / 'environ').read_bytes().decode().split('\0') if '=' in part)
        if Path(env.get('HOME', '/')).resolve() != home:
            return None
        return fields[19]
    except (OSError, UnicodeError, ValueError, IndexError):
        return None


def stop_processes(binary, home, uid):
    binary, home = Path(binary).resolve(), Path(home).resolve()
    running = {}
    for process in Path('/proc').iterdir():
        if process.name.isdigit() and int(process.name) != os.getpid():
            identity = process_identity(process, home, uid, binary)
            if identity:
                running[int(process.name)] = identity
    handles = {}
    try:
        for pid, identity in running.items():
            try:
                if hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'):
                    try:
                        handles[pid] = os.pidfd_open(pid)
                    except OSError as error:
                        if error.errno not in (errno.ENOSYS, errno.EINVAL):
                            raise
                if process_identity(Path('/proc') / str(pid), home, uid, binary) != identity:
                    continue
                if pid in handles:
                    signal.pidfd_send_signal(handles[pid], signal.SIGTERM)
                else:
                    os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
        deadline = time.monotonic() + 5
        alive = dict(running)
        while alive and time.monotonic() < deadline:
            alive = {pid:identity for pid,identity in alive.items()
                     if process_identity(Path('/proc') / str(pid), home, uid, binary) == identity}
            if alive:
                time.sleep(0.1)
        for pid, identity in alive.items():
            if process_identity(Path('/proc') / str(pid), home, uid, binary) == identity:
                try:
                    if pid in handles:
                        signal.pidfd_send_signal(handles[pid], signal.SIGKILL)
                    else:
                        os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 2
        while alive and time.monotonic() < deadline:
            alive = {pid:identity for pid,identity in alive.items()
                     if process_identity(Path('/proc') / str(pid), home, uid, binary) == identity}
            if alive:
                time.sleep(0.1)
        reopened = any(p.name.isdigit() and int(p.name) != os.getpid() and process_identity(p, home, uid, binary)
                       for p in Path('/proc').iterdir())
        if alive or reopened:
            raise ValueError('可能是 CLI 被其他服务重新启动或尚未关闭，已暂停更换登录配置')
        return len(running)
    finally:
        for handle in handles.values():
            os.close(handle)


if __name__ == '__main__':
    # Used by a root service as a short-lived worker with the native user's UID.
    home, uid, binary = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    if os.geteuid() != uid:
        raise SystemExit('CLI process worker user does not match the runtime owner')
    print(json.dumps({'stopped': stop_processes(binary, home, uid)}))
