"""Fixed account archives and a single working credential; no history snapshots."""
import json
import os
import tempfile
from pathlib import Path

TOKEN='jetski-standalone-oauth-token'


def read_bytes(path):
    path=Path(path)
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):raise ValueError('登录存档不能经过符号链接')
    if not path.exists():return None
    if not path.is_file() or path.stat().st_size>1024*1024:raise ValueError('登录凭据文件格式或大小异常')
    return path.read_bytes()


def write_bytes(path,value,uid=None):
    path=Path(path)
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):raise ValueError('登录文件路径不能经过符号链接')
    if value is None:
        path.unlink(missing_ok=True);return
    if len(value)>1024*1024:raise ValueError('登录凭据不能超过 1MB')
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,temporary=tempfile.mkstemp(dir=path.parent,prefix='.credential-')
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(value);stream.flush();os.fsync(stream.fileno())
        os.chmod(temporary,0o600)
        if uid is not None and hasattr(os,'chown'):
            import pwd
            gid=pwd.getpwuid(uid).pw_gid
            os.chown(temporary,uid,gid)
            if path.parent.stat().st_uid==os.geteuid():os.chown(path.parent,uid,gid)
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def archive_bytes(manager,identifier):
    folder=manager.profile_dir(identifier)
    archive=folder/'store'/TOKEN
    value=read_bytes(archive)
    if value is None:
        value=read_bytes(folder/'home/.gemini'/TOKEN)
    return value


def working_file(home):return Path(home)/'.gemini'/TOKEN


def save_current(manager,identifier):
    profile=manager.get_profile(identifier)
    if not profile:return False
    if manager.bound_profile not in (None,identifier):return False
    binding=manager.native_runtime.binding
    if binding and binding['profile_id']!=identifier and profile.get('kind')!='existing':return False
    current=working_file(manager.home_for(identifier))
    token=manager.installed_token(current)
    if not token:return False
    if profile.get('kind')!='existing' and not manager.account_matches(identifier,token):return False
    value=read_bytes(current)
    if manager.installed_token(current)!=token:raise ValueError('当前凭据仍在更新，请稍后重试')
    write_bytes(manager.profile_dir(identifier)/'store'/TOKEN,value)
    return True
