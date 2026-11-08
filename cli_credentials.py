"""Use the desktop working credential as the Linux CLI file-storage fallback."""
import datetime
import mmap
import os
import subprocess
import sys
import shutil
import tempfile
import time
from pathlib import Path

from config import atomic_json


class CliCredentials:
    def __init__(self, manager):
        self.manager = manager
        self._binaries = {}

    def binary(self, home):
        if os.name != 'posix':
            return None
        candidates = [Path(home) / '.local/bin/agy']
        found = shutil.which('agy')
        if found:
            candidates.append(Path(found))
        for candidate in candidates:
            try:
                if candidate.is_file():
                    with candidate.open('rb') as stream:
                        header = stream.read(128)
                    if header.startswith(b'#!/bin/sh\n# Antigravity Suite managed CLI launcher v1\n'):
                        candidate = candidate.with_name('agy.suite-real')
                path = candidate.resolve()
                info = path.stat()
                key = (str(path), info.st_size, info.st_mtime_ns)
                if key not in self._binaries:
                    recognized = False
                    if 0 < info.st_size < 1024 * 1024 * 1024:
                        with path.open('rb') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
                            recognized = (data[:4] == b'\x7fELF' and data.find(b'CLITokenFilePath') >= 0
                                          and data.find(b'cliFileTokenStorage') >= 0)
                    if len(self._binaries) >= 8:
                        self._binaries.clear()
                    self._binaries[key] = recognized
                if self._binaries[key]:
                    return path
            except (OSError, ValueError):
                continue
        return None

    @staticmethod
    def paths(spec):
        home = Path(spec['native_home']).resolve()
        root = home / '.gemini'
        folder = root / 'antigravity-cli'
        source = root / 'jetski-standalone-oauth-token'
        alias = folder / 'antigravity-oauth-token'
        if any(path.is_symlink() for path in (root, folder, source)):
            raise ValueError('CLI 共用登录路径不能指向其他目录或文件')
        if alias.is_symlink() and alias.resolve() != source:
            raise ValueError('CLI 登录文件已指向其他位置，未覆盖；请检查原链接')
        return source, alias

    def backup(self, path):
        folder = self.manager.data_dir / 'cli-credential-backups' / str(time.time_ns())
        folder.mkdir(parents=True, mode=0o700)
        raw = path.read_bytes()
        fd = os.open(folder / path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
        return raw

    @staticmethod
    def expiry(value):
        try:
            parsed = datetime.datetime.fromisoformat(value['token']['expiry'].replace('Z', '+00:00'))
            return parsed.replace(tzinfo=datetime.timezone.utc).timestamp() if parsed.tzinfo is None else parsed.timestamp()
        except (KeyError, TypeError, ValueError, OverflowError):
            return 0

    def ensure(self, spec, prefer_native=False):
        if not self.binary(spec['native_home']):
            return False
        source, alias = self.paths(spec)
        if alias.is_symlink():
            return True
        import json
        native = self.manager.installed_token(source)
        if source.exists() and native is None:
            # A concurrent token write must not be mistaken for a fresh login or logout.
            return False
        source_before = source.read_bytes() if source.exists() else None
        alias_before = None
        adopt = False
        if alias.exists():
            if not alias.is_file() or alias.stat().st_size > 1024 * 1024:
                raise ValueError('CLI 登录文件格式或大小异常，未覆盖')
            cli = self.manager.installed_token(alias)
            if not cli:
                raise ValueError('CLI 已有凭据不是兼容的 Google 登录，未覆盖')
            alias_before = alias.read_bytes()
            if native and cli['fingerprint'] != native['fingerprint'] and not prefer_native:
                raise ValueError('CLI 与桌面当前登录不同，均已保留；请从面板切换并启动要共用的配置')
            adopt = not native or (cli['fingerprint'] == native['fingerprint']
                                   and self.expiry(json.loads(alias_before)) > self.expiry(json.loads(source_before)))
            if alias.read_bytes() != alias_before:
                raise ValueError('CLI 凭据正在更新，请稍后重试共用登录')
            self.backup(alias)
        uid = spec['native_uid']
        import pwd
        gid = pwd.getpwuid(uid).pw_gid
        if adopt:
            if source_before is not None:
                self.backup(source)
            if source.exists() and source.read_bytes() != source_before:
                raise ValueError('桌面凭据正在更新，请稍后重试 CLI 共用登录')
            atomic_json(source, json.loads(alias_before))
            os.chown(source, uid, gid)
        root_created = not source.parent.exists()
        folder_created = not alias.parent.exists()
        alias.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root_created:
            os.chown(source.parent, uid, gid)
        if folder_created:
            os.chown(alias.parent, uid, gid)
        fd, temporary = tempfile.mkstemp(dir=alias.parent, prefix='.shared-cli-login-')
        os.close(fd)
        os.unlink(temporary)
        try:
            os.symlink(str(source), temporary)
            os.lchown(temporary, uid, gid)
            if alias_before is not None and (not alias.is_file() or alias.read_bytes() != alias_before):
                raise ValueError('CLI 凭据正在更新，请稍后重试共用登录')
            os.replace(temporary, alias)
        finally:
            if os.path.lexists(temporary):
                os.unlink(temporary)
        return True

    def stop(self, spec):
        binary = self.binary(spec['native_home'])
        if not binary or not Path('/proc').is_dir():
            return 0
        uid = spec['native_uid']
        if os.geteuid() != uid:
            # Docker root may lack CAP_SYS_PTRACE, so /proc access must use the CLI owner.
            import json
            import pwd
            worker = Path(__file__).with_name('cli_processes.py').read_text(encoding='utf-8')
            result = subprocess.run([getattr(sys, '_base_executable', sys.executable), '-B', '-c', worker,
                                     spec['native_home'], str(uid), str(binary)],
                                    capture_output=True, text=True, timeout=12, user=uid,
                                    group=pwd.getpwuid(uid).pw_gid, cwd=spec['native_home'],
                                    env=dict(os.environ,HOME=spec['native_home']))
            if result.returncode:
                raise ValueError('可能是 CLI 关闭权限不足、尚未结束或被重新启动，未更换登录配置')
            return json.loads(result.stdout)['stopped']
        from cli_processes import stop_processes
        return stop_processes(binary, spec['native_home'], uid)
