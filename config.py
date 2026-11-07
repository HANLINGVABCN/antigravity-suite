import hashlib
import hmac
import json
import os
import secrets
import tempfile
import platform
from pathlib import Path

SUITE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get('ANTIGRAVITY_SUITE_DATA', str(SUITE_DIR / 'data'))).resolve()
PROFILES_DIR = DATA_DIR / 'profiles'
CONFIG_FILE = DATA_DIR / 'config.json'

def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 300000).hex()
    return 'pbkdf2_sha256$300000$' + salt + '$' + digest

def check_password(password, stored):
    try:
        alg, rounds, salt, expected = stored.split('$')
        if alg != 'pbkdf2_sha256' or not 100000 <= int(rounds) <= 1000000: return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, AttributeError): return False

def generate_secure_password(length=20):
    return secrets.token_urlsafe(length)[:length]

def load_config():
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    artifact='linux-arm' if platform.machine().lower() in ('aarch64','arm64') else 'linux-x64'
    existing='/opt/Antigravity/antigravity' if Path('/opt/Antigravity/antigravity').is_file() else '/opt/antigravity/antigravity'
    cfg = dict(listen_host='127.0.0.1', listen_port=18888, auth_user='admin',
               auth_password_hash='', session_secret=secrets.token_hex(32),
               mfa_enabled=False,mfa_secret='',mfa_recovery_hashes=[],mfa_last_counter=-1,
               default_profile_id=None, active_profile_id=None,
               antigravity_bin=existing, display_num=':99',
               runtime_home=str(DATA_DIR / 'runtime-home'), profiles_dir=str(PROFILES_DIR),
               backend_scheme='https', backend_verify_tls=False, trusted_proxy=False,
               cookie_secure='auto', core_deb_url='https://storage.googleapis.com/antigravity-public/antigravity-hub/2.29.0-5723181030440960/'+artifact+'/Antigravity.deb')
    if CONFIG_FILE.exists():
        with CONFIG_FILE.open(encoding='utf-8') as f: saved = json.load(f)
        cfg.update(saved)
        if 'profiles_dir' not in saved and (SUITE_DIR / 'profiles').is_dir():
            cfg['profiles_dir'] = str(SUITE_DIR / 'profiles')
    old_password = cfg.pop('auth_pass', '')
    if cfg.get('mfa_enabled') and not cfg.get('mfa_secret'):raise ValueError('验证器配置损坏：缺少密钥，请恢复私有备份')
    if not cfg['auth_password_hash']:
        password = old_password or generate_secure_password()
        cfg['auth_password_hash'] = hash_password(password)
        if not old_password:
            path = DATA_DIR / 'initial-credentials.txt'
            atomic_json(path, {'username':cfg['auth_user'], 'password':password})
            print('首次管理员凭据已写入 ' + str(path) + '（权限 600）')
    if not 1 <= int(cfg['listen_port']) <= 65535: raise ValueError('监听端口必须在 1–65535 范围内')
    save_config(cfg)
    return cfg

def save_config(cfg):
    atomic_json(CONFIG_FILE, cfg)
