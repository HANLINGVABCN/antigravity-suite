"""Check JavaScript syntax without executing app code or contacting services."""
import re
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
for path in (root / 'static').glob('*.js'):
    subprocess.run(['node', '--check', str(path)], check=True)
for path in (root / 'static').glob('*.html'):
    scripts = re.findall(r'<script\b[^>]*>(.*?)</script>', path.read_text(encoding='utf-8'), re.S | re.I)
    for source in scripts:
        if source.strip():
            subprocess.run(['node', '--check'], input=source, encoding='utf-8', check=True)
print('Frontend JavaScript syntax checks passed')
