"""Launch the official CLI with the current desktop runtime's proxy environment."""
import argparse
import json
import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit

PROXY_KEYS = ('http_proxy', 'https_proxy', 'all_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY')


def launch(runtime, arguments):
    path = Path(runtime)
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError('CLI 共用代理配置的权限异常，请在面板重新启动配置')
    value = json.loads(path.read_text(encoding='utf-8'))
    uid, gid = value['uid'], value['gid']
    if not isinstance(uid, int) or isinstance(uid, bool) or not isinstance(gid, int) or isinstance(gid, bool):
        raise ValueError('CLI 运行用户无效')
    if path.stat().st_uid != uid or os.geteuid() not in (0, uid):
        raise ValueError('CLI 必须由原反重力运行用户启动')
    binary = Path(value['binary'])
    home = Path(value['home']).resolve()
    if binary != home / '.local/bin/agy.suite-real' or not binary.is_file() or binary.stat().st_mode & 0o6000:
        raise ValueError('官方 CLI 入口不存在，请重新安装 CLI')
    environment = dict(os.environ)
    for key in ('SSH_CLIENT', 'SSH_CONNECTION'):
        environment.pop(key, None)
    for key in PROXY_KEYS:
        environment.pop(key, None)
    environment.update(HOME=str(home), XDG_CONFIG_HOME=str(home / '.config'),
                       XDG_DATA_HOME=str(home / '.local/share'), XDG_CACHE_HOME=str(home / '.cache'),
                       NO_PROXY='localhost,127.0.0.1,::1', no_proxy='localhost,127.0.0.1,::1')
    help_only = '--help' in arguments or '-h' in arguments or (arguments and arguments[0] == 'help')
    if not help_only:
        if not value.get('ready'):
            raise ValueError('共用代理尚未就绪，请启动后台并在面板载入所需配置；未改走直连')
        if value.get('proxy_required'):
            proxy = value.get('proxy_url', '')
            parsed = urlsplit(proxy)
            if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.username or parsed.path or parsed.query:
                raise ValueError('共用代理入口无效，已阻止 CLI 启动；未改走直连')
            try:
                with socket.create_connection(('127.0.0.1', parsed.port), timeout=5) as connection:
                    connection.sendall(b'CONNECT oauth2.googleapis.com:443 HTTP/1.1\r\nHost: oauth2.googleapis.com:443\r\n\r\n')
                    response = connection.recv(1024).split(b'\r\n', 1)[0].split()
                    if len(response) < 2 or response[1] != b'200':
                        raise OSError('Shared proxy could not reach Google')
            except OSError as error:
                raise ValueError('可能是共用代理桥尚未启动或不可连接，已阻止 CLI 启动；未改走直连') from error
            for key in PROXY_KEYS:
                environment[key] = proxy
    if os.geteuid() != uid:
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)
    if not os.access(os.getcwd(), os.R_OK | os.X_OK):
        raise ValueError('当前目录不能由反重力原运行用户访问，请切换到可访问的项目目录')
    os.execve(str(binary), [str(binary)] + arguments, environment)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', required=True)
    options, arguments = parser.parse_known_args()
    if arguments[:1] == ['--']:
        arguments = arguments[1:]
    try:
        launch(options.runtime, arguments)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print('CLI 未启动：' + str(error), file=sys.stderr)
        raise SystemExit(1)
