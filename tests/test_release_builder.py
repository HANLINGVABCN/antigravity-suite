import hashlib
import re
import subprocess
from pathlib import Path

from suite_updater import extract_release
from tools.build_release import build_release, extract_changelog_notes


def test_extract_changelog_notes_reads_matching_section(tmp_path):
    changelog = tmp_path / 'CHANGELOG.md'
    changelog.write_text(
        '# Changelog\n\n## 0.10.1 — 2026-11-24\n\n- First item\n- Second item\n\n## 0.10.0 — 2026-11-10\n\n- Older\n',
        encoding='utf-8',
    )
    notes = extract_changelog_notes(changelog, '0.10.1')
    assert '- First item' in notes and '- Second item' in notes and '- Older' not in notes


def test_build_release_produces_updater_compatible_archive(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    for rel, content in [
        ('VERSION', '0.10.1\n'),
        ('CHANGELOG.md', '# Changelog\n\n## 0.10.1 — 2026-11-24\n\n- Fix v2.34.0\n'),
        ('server.py', 'print("ok")\n'),
        ('config.py', 'X = 1\n'),
        ('requirements.txt', 'aiohttp\n'),
        ('update_worker.py', 'pass\n'),
        ('service_control.py', 'pass\n'),
        ('static/portal.html', '<html></html>\n'),
    ]:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
    subprocess.run(['git', 'init', '-q'], cwd=repo, check=True)
    subprocess.run(['git', 'add', '.'], cwd=repo, check=True)

    out = tmp_path / 'dist'
    info = build_release(repo, out, expected_tag='v0.10.1')
    name = 'antigravity-suite-v0.10.1-source.zip'
    assert info['archive'].name == name
    checksum_text = info['checksum'].read_text(encoding='utf-8').strip()
    assert re.fullmatch(r'[a-fA-F0-9]{64}\s+\*?' + re.escape(name), checksum_text)
    assert checksum_text.split()[0] == hashlib.sha256(info['archive'].read_bytes()).hexdigest()
    extracted = extract_release(info['archive'], tmp_path / 'extracted', '0.10.1')
    assert 'server.py' in extracted and 'static/portal.html' in extracted
