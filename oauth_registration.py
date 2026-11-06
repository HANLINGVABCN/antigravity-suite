"""Resolve desktop OAuth registration from a user's installed official client."""
import hashlib
import mmap
import os
from functools import lru_cache
from pathlib import Path

# Public application identifier; no application secret is distributed here.
DESKTOP_CLIENT_ID = '1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com'
SECRET_FINGERPRINT = '1d2f041093fd95aa8995a038c711d50a7960da09a505381c09a745d6ad0ecc60'
SECRET_LENGTH = 35


@lru_cache(maxsize=4)
def _read_registration(path, size, mtime_ns):
    if not 0 < size < 1024 * 1024 * 1024:
        return None
    with Path(path).open('rb') as file, mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as data:
        if data.find(DESKTOP_CLIENT_ID.encode()) < 0:
            return None
        offset = 0
        while True:
            offset = data.find(b'GO' + b'CSPX-', offset)
            if offset < 0:
                return None
            candidate = data[offset:offset + SECRET_LENGTH]
            if hashlib.sha256(candidate).hexdigest() == SECRET_FINGERPRINT:
                return DESKTOP_CLIENT_ID, candidate.decode('ascii')
            offset += 7


def resolve_registration(cfg):
    identifier = cfg.get('oauth_client_id') or os.environ.get('ANTIGRAVITY_OAUTH_CLIENT_ID')
    secret = cfg.get('oauth_client_secret') or os.environ.get('ANTIGRAVITY_OAUTH_CLIENT_SECRET')
    if identifier:
        return identifier, secret
    root = Path(cfg.get('antigravity_bin', '/opt/Antigravity/antigravity')).parent
    for name in ['resources/bin/language_server', 'resources/bin/language_server_linux_x64',
                 'resources/bin/language_server_linux_arm']:
        path = root / name
        if not path.is_file():
            continue
        info = path.stat()
        found = _read_registration(str(path.resolve()), info.st_size, info.st_mtime_ns)
        if found:
            return found
    raise ValueError('未能识别本机官方客户端的授权配置。请安装兼容的 Antigravity 本体、导入已有档案，或填写有权使用的 OAuth 配置。详见 docs/oauth.md')
