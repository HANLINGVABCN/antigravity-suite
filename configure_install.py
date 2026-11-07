"""Prepare installation choices before stopping a service; never reset existing auth."""
import argparse
import getpass
import json
import re
import socket
import sys
from pathlib import Path

from config import DATA_DIR, atomic_json, default_config, generate_secure_password, hash_password


def valid_port(value):
    if isinstance(value, (bool, float)) or not str(value).isascii() or not str(value).isdigit():
        raise ValueError('端口必须是 1–65535 的整数')
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError('端口必须是 1–65535 的整数')
    return port


def read_existing(data_dir):
    path = Path(data_dir) / 'config.json'
    if not path.exists():
        markers = ('initial-credentials.txt', 'profiles.json', 'proxies.json', 'profiles', 'runtime-home', 'shared-history')
        if any((path.parent / name).exists() for name in markers):
            raise ValueError('发现已有数据但缺少 config.json，请恢复私有备份；不会生成新密码覆盖原设置')
        return None
    try:
        saved = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(saved, dict) or not isinstance(saved.get('auth_user'), str) or not saved['auth_user'].strip():
            raise ValueError()
        valid_port(saved.get('listen_port', 18888))
        password_hash = saved.get('auth_password_hash', '')
        if password_hash:
            algorithm, rounds, salt, digest = password_hash.split('$')
            if algorithm != 'pbkdf2_sha256' or not 100000 <= int(rounds) <= 1000000:
                raise ValueError()
            if len(bytes.fromhex(salt)) != 16 or len(bytes.fromhex(digest)) != 32:
                raise ValueError()
        elif not isinstance(saved.get('auth_pass'), str) or not saved['auth_pass']:
            raise ValueError()
        if saved.get('mfa_enabled') and not saved.get('mfa_secret'):
            raise ValueError()
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise ValueError('已有配置或管理员凭据无效，请恢复私有备份；不会重置密码或验证器') from error
    return saved


def ask_choice(prompt, choices, default, reader, writer):
    while True:
        value = reader(prompt).strip() or default
        if value in choices:
            return value
        writer('请选择 ' + ' / '.join(choices))


def ask_port(default, reader, writer):
    while True:
        try:
            return valid_port(reader('后台监听端口 [%s]：' % default).strip() or str(default))
        except ValueError as error:
            writer(str(error))


def prepare_install(data_dir, non_interactive=False, reader=input, password_reader=getpass.getpass, writer=print):
    data_dir = Path(data_dir).resolve()
    saved = read_existing(data_dir)
    plan = dict(version=1, data_dir=str(data_dir), mode='existing' if saved is not None else 'new', updates={})
    if saved is not None:
        writer('检测到已有配置：默认保留端口、账号、密码、验证器和全部档案。')
        if not non_interactive:
            choice = ask_choice('1. 保留设置升级  2. 仅修改监听端口后升级 [1]：', ('1', '2'), '1', reader, writer)
            if choice == '2':
                plan['original_port'] = valid_port(saved.get('listen_port', 18888))
                plan['updates']['listen_port'] = ask_port(plan['original_port'], reader, writer)
            writer('账号密码和验证器可在网页“服务设置”中修改。')
        return plan

    cfg = default_config(data_dir)
    if not non_interactive:
        writer('首次安装：回车使用方括号内的默认选项。')
        cfg['listen_port'] = ask_port(cfg['listen_port'], reader, writer)
        while True:
            user = reader('管理员用户名（英文字母、数字或 ._@-，最多 64 个字符）[admin]：').strip() or 'admin'
            if re.fullmatch(r'[A-Za-z0-9_.@-]{1,64}', user):
                cfg['auth_user'] = user
                break
            writer('用户名请使用 1–64 个英文字母、数字或 ._@-')
        mode = ask_choice('密码：1. 随机生成  2. 自己设置 [1]：', ('1', '2'), '1', reader, writer)
    else:
        mode = '1'
    if mode == '2':
        while True:
            password = password_reader('管理员密码（12–1024 个字符，输入不显示）：')
            if not 12 <= len(password) <= 1024:
                writer('密码长度必须在 12–1024 个字符之间')
                continue
            if password != password_reader('再次输入管理员密码：'):
                writer('两次输入的密码不一致，请重新设置')
                continue
            break
    else:
        password = generate_secure_password()
        plan['initial_credentials'] = dict(username=cfg['auth_user'], password=password)
    cfg['auth_password_hash'] = hash_password(password)
    plan['config'] = cfg
    return plan


def apply_install(plan, data_dir):
    data_dir = Path(data_dir).resolve()
    if plan.get('version') != 1 or plan.get('data_dir') != str(data_dir):
        raise ValueError('安装选项与目标数据目录不匹配')
    saved = read_existing(data_dir)
    if plan.get('mode') == 'existing':
        if saved is None:
            raise ValueError('原配置已消失，请恢复私有备份后再升级')
        updates = plan.get('updates', {})
        if not isinstance(updates, dict) or set(updates) - {'listen_port'}:
            raise ValueError('升级向导只允许调整端口，不能覆盖账号密码或验证器')
        if updates:
            if valid_port(saved.get('listen_port', 18888)) != plan.get('original_port'):
                raise ValueError('监听端口在选择后已被修改，请重新运行安装向导')
            changed = dict(saved, listen_port=valid_port(updates['listen_port']))
            if changed != saved:
                atomic_json(data_dir / 'config.json', changed)
            saved = changed
        # Reread after service shutdown so even a recently changed password is kept.
        return saved
    if plan.get('mode') != 'new' or saved is not None:
        raise ValueError('目标已有配置，拒绝用首次安装选项覆盖')
    cfg = plan['config']
    valid_port(cfg['listen_port'])
    credentials = plan.get('initial_credentials')
    credentials_path = data_dir / 'initial-credentials.txt'
    if credentials:
        atomic_json(credentials_path, credentials)
    try:
        atomic_json(data_dir / 'config.json', cfg)
    except Exception:
        if credentials:
            credentials_path.unlink(missing_ok=True)
        raise
    return cfg


def check_listen_port(plan, data_dir):
    data_dir=Path(data_dir).resolve()
    if plan.get('version')!=1 or plan.get('data_dir')!=str(data_dir):
        raise ValueError('安装选项与目标数据目录不匹配')
    cfg=read_existing(data_dir) if plan['mode']=='existing' else plan['config']
    if cfg is None:
        raise ValueError('原配置已消失，请恢复私有备份')
    host=cfg.get('listen_host','127.0.0.1')
    port=valid_port(plan.get('updates',{}).get('listen_port',cfg.get('listen_port',18888)))
    try:
        for family,kind,proto,_,address in socket.getaddrinfo(host,port,type=socket.SOCK_STREAM):
            with socket.socket(family,kind,proto) as listener:
                listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
                listener.bind(address)
    except OSError as error:
        raise ValueError('监听地址或端口无法使用，请检查地址和占用情况后重新选择端口') from error


def show_summary(plan, cfg):
    host = cfg.get('listen_host', '127.0.0.1')
    if ':' in host:
        host = '[' + host + ']'
    print('后台地址：http://%s:%s/?portal=1' % (host, cfg.get('listen_port', 18888)))
    print('管理员用户名：' + cfg['auth_user'])
    if plan['mode'] == 'existing':
        print('管理员密码与验证器：保留原设置')
    elif plan.get('initial_credentials'):
        print('随机密码文件：' + str(Path(plan['data_dir']) / 'initial-credentials.txt') + '（权限 600）')
    else:
        print('管理员密码：使用刚才自己设置的密码，不记录明文')


def main(argv=None):
    parser = argparse.ArgumentParser(description='后台安装配置向导；已有账号密码和验证器始终保留')
    parser.add_argument('--data-dir', type=Path, default=DATA_DIR, help='私有数据目录')
    parser.add_argument('--non-interactive', action='store_true', help='首次使用默认端口/admin/随机密码；已有配置保持原值')
    phases = parser.add_mutually_exclusive_group()
    phases.add_argument('--prepare', type=Path, help='只将选项写入权限 600 的临时计划，不修改现有数据')
    phases.add_argument('--apply', type=Path, help='服务停止后应用之前的安装选项')
    phases.add_argument('--check-port', type=Path, help='服务停止后验证所选端口可用，不修改数据')
    args = parser.parse_args(argv)
    try:
        if args.check_port:
            plan=json.loads(args.check_port.read_text(encoding='utf-8'))
            check_listen_port(plan,args.data_dir)
            print('所选监听地址和端口可用。')
            return 0
        if args.apply:
            plan = json.loads(args.apply.read_text(encoding='utf-8'))
            cfg = apply_install(plan, args.data_dir)
        else:
            if not args.non_interactive and not sys.stdin.isatty():
                raise ValueError('没有可用的交互终端；自动安装请明确添加 --non-interactive')
            print('Antigravity 后台安装向导')
            plan = prepare_install(args.data_dir, args.non_interactive)
            if args.prepare:
                atomic_json(args.prepare, plan)
                cfg = plan.get('config') or dict(read_existing(args.data_dir), **plan['updates'])
            else:
                cfg = apply_install(plan, args.data_dir)
        show_summary(plan, cfg)
        return 0
    except (EOFError, KeyboardInterrupt):
        print('\n已取消，未应用安装选项。', file=sys.stderr)
        return 130
    except (ValueError, OSError, TypeError, KeyError) as error:
        print('安装配置失败：' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
