"""Reject accidental credentials and runtime data in tracked public files."""
import re
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
private_dirs = {'data', 'profiles', 'runtime-home', 'shared-history', 'file-backups', 'backups', '.venv'}
private_names = {'config.json', 'profiles.json', 'proxies.json', 'initial-credentials.txt',
                 'jetski-standalone-oauth-token', 'antigravity-recovery-codes.txt', '.env'}
patterns = [
    re.compile(b'GO' + b'CSPX-' + rb'[A-Za-z0-9_-]{15,}'),
    re.compile(b'gh' + rb'[opusr]_[A-Za-z0-9]{20,}'),
    re.compile(b'github_' + rb'pat_[A-Za-z0-9_]{20,}'),
    re.compile(b'-----BEGIN ' + rb'(?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----'),
    re.compile(b'AI' + b'za' + rb'[A-Za-z0-9_-]{30,}'),
]
result = subprocess.run(['git', 'ls-files', '-z'], cwd=root, capture_output=True, check=True)
files = [Path(name) for name in result.stdout.decode('utf-8').split('\0') if name]
failures = []
for relative in files:
    if private_dirs.intersection(relative.parts) or relative.name in private_names:
        failures.append(str(relative) + ': private runtime file')
    if relative.suffix in {'.pem', '.key', '.p12', '.pfx', '.db', '.deb', '.AppImage'}:
        failures.append(str(relative) + ': private/binary runtime artifact')
    path = root / relative
    if path.is_symlink():
        failures.append(str(relative) + ': symlink outside public source')
    elif path.is_file() and any(pattern.search(path.read_bytes()) for pattern in patterns):
        failures.append(str(relative) + ': possible credential (value omitted)')
if failures:
    raise SystemExit('\n'.join(failures))
print('Public source tree check passed: %d tracked files' % len(files))
