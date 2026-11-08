"""Keep a reversible CLI entrypoint and publish only the shared loopback proxy URL."""
import hashlib
import json
import os
import pwd
import shlex
import sys
import tempfile
from pathlib import Path

from config import atomic_json

MARKER = '# Antigravity Suite managed CLI launcher v1'


class CliProxy:
    def __init__(self, manager):
        self.manager = manager
        self.registry = manager.data_dir / 'cli-launchers.json'
        self.records = json.loads(self.registry.read_text()) if self.registry.is_file() else {}

    @staticmethod
    def runtime_path(spec):
        return Path(spec['native_home']) / '.local/share/antigravity-suite/cli-runtime.json'

    def publish(self, spec, proxy_url=None, required=False, ready=True, profile_id=None):
        home = Path(spec['native_home']).resolve()
        entry = home / '.local/bin/agy'
        if not entry.is_file() or not self.manager.cli_credentials.binary(home):
            return False
        if entry.stat().st_mode & 0o6000:
            raise ValueError('CLI 可执行权限异常，未接管启动入口')
        real = entry.with_name('agy.suite-real')
        record = self.records.get(str(home))
        current = entry.read_bytes()
        managed = current.startswith(('#!/bin/sh\n' + MARKER + '\n').encode())
        if managed:
            if not record or hashlib.sha256(current).hexdigest() != record['wrapper_sha256']:
                raise ValueError('CLI 启动入口被其他操作修改，未覆盖')
        elif real.exists() or real.is_symlink():
            raise ValueError('CLI 备用入口已存在，未覆盖；请检查原安装')
        runtime = self.runtime_path(spec)
        for folder in (home / '.local', home / '.local/share', runtime.parent):
            if folder.is_symlink():
                raise ValueError('CLI 共用代理目录不能指向其他目录')
            if not folder.exists():
                folder.mkdir(mode=0o700)
                os.chown(folder, spec['native_uid'], pwd.getpwuid(spec['native_uid']).pw_gid)
        interpreter = getattr(sys, '_base_executable', sys.executable)
        driver = runtime.parent / 'launcher.py'
        driver_content = Path(__file__).with_name('cli_launcher.py').read_bytes()
        if driver.is_symlink():
            raise ValueError('CLI 启动器文件不能是符号链接')
        if driver.is_file():
            existing_driver = hashlib.sha256(driver.read_bytes()).hexdigest()
            expected_driver = (record or {}).get('driver_sha256', hashlib.sha256(driver_content).hexdigest())
            if existing_driver != expected_driver:
                raise ValueError('CLI 启动器被其他操作修改，未覆盖')
        if not driver.is_file() or driver.read_bytes() != driver_content:
            fd, temporary = tempfile.mkstemp(dir=runtime.parent, prefix='.cli-driver-')
            try:
                with os.fdopen(fd, 'wb') as stream:stream.write(driver_content)
                os.chmod(temporary, 0o600)
                os.chown(temporary, spec['native_uid'], pwd.getpwuid(spec['native_uid']).pw_gid)
                os.replace(temporary, driver)
            finally:
                if os.path.exists(temporary):os.unlink(temporary)
        command = shlex.quote(interpreter) + ' -B ' + shlex.quote(str(driver)) + ' --runtime ' + shlex.quote(str(runtime)) + ' -- "$@"'
        script = '#!/bin/sh\n' + MARKER + '\n'
        if spec['native_uid']:
            if not Path('/usr/bin/setpriv').is_file():raise ValueError('原用户 CLI 启动器需要 util-linux 的 setpriv，尚未接管')
            script += 'if [ "$(/usr/bin/id -u)" = 0 ]; then\n  exec /usr/bin/setpriv --reuid='+str(spec['native_uid'])+' --regid='+str(pwd.getpwuid(spec['native_uid']).pw_gid)+' --clear-groups '+command+'\nfi\n'
        script += 'exec ' + command + '\n'
        content = script.encode()
        if not managed:
            self.manager.cli_credentials.stop(spec)
            os.replace(entry, real)
        if current != content:
            fd, temporary = tempfile.mkstemp(dir=entry.parent, prefix='.suite-cli-launcher-')
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(content)
                os.chmod(temporary, 0o755)
                os.chown(temporary, spec['native_uid'], pwd.getpwuid(spec['native_uid']).pw_gid)
                os.replace(temporary, entry)
            except Exception:
                if not managed and real.exists():
                    os.replace(real, entry)
                raise
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        self.records[str(home)] = dict(native_home=str(home), native_uid=spec['native_uid'], wrapper_sha256=hashlib.sha256(content).hexdigest(),driver_sha256=hashlib.sha256(driver_content).hexdigest())
        atomic_json(self.registry, self.records)
        value = dict(version=1, home=str(home), uid=spec['native_uid'], gid=pwd.getpwuid(spec['native_uid']).pw_gid,
                     binary=str(real), proxy_url=proxy_url or '', proxy_required=bool(required), ready=bool(ready), profile_id=profile_id)
        if runtime.is_symlink():
            raise ValueError('CLI 共用代理状态不能是符号链接')
        if not runtime.is_file() or json.loads(runtime.read_text()) != value:
            atomic_json(runtime, value)
            os.chown(runtime, spec['native_uid'], value['gid'])
        return True

    def suspend(self):
        for record in self.records.values():
            runtime = self.runtime_path(record)
            if runtime.is_file() and not runtime.is_symlink():
                value = json.loads(runtime.read_text())
                value['ready'] = False
                atomic_json(runtime, value)
                os.chown(runtime, record['native_uid'], pwd.getpwuid(record['native_uid']).pw_gid)

    def restore(self):
        restored = 0
        for home, record in list(self.records.items()):
            entry = Path(home) / '.local/bin/agy'
            real = entry.with_name('agy.suite-real')
            if not entry.is_file() or hashlib.sha256(entry.read_bytes()).hexdigest() != record['wrapper_sha256']:
                raise ValueError('CLI 入口已发生其他修改，未覆盖；请保留安装备份')
            driver = self.runtime_path(record).parent / 'launcher.py'
            if driver.is_symlink() or not driver.is_file() or hashlib.sha256(driver.read_bytes()).hexdigest() != record['driver_sha256']:
                raise ValueError('CLI 启动器已发生其他修改，未覆盖；请保留安装备份')
            self.manager.cli_credentials.stop(record)
            os.replace(real, entry)
            runtime = self.runtime_path(record)
            if runtime.is_file() and not runtime.is_symlink():
                runtime.unlink()
            driver.unlink()
            self.records.pop(home)
            restored += 1
            atomic_json(self.registry, self.records)
        return restored


if __name__ == '__main__':
    from config import DATA_DIR
    from types import SimpleNamespace
    from cli_credentials import CliCredentials
    manager = SimpleNamespace(data_dir=DATA_DIR)
    manager.cli_credentials = CliCredentials(manager)
    print('已恢复 CLI 启动入口：' + str(CliProxy(manager).restore()))
