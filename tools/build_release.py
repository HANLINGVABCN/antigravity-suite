"""Build and self-verify a GitHub Release source archive compatible with suite_updater.py."""
import argparse
import hashlib
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from suite_updater import extract_release, safe_source_name, version_tuple


def extract_changelog_notes(changelog_path, version):
    if not changelog_path.is_file():
        return f'Release v{version}\n'
    text = changelog_path.read_text(encoding='utf-8')
    pattern = re.compile(
        r'^##\s+v?' + re.escape(version) + r'(?:\s+[^\n]*)?\n(.*?)(?=^##\s+|\Z)',
        re.M | re.S,
    )
    match = pattern.search(text)
    if match and match.group(1).strip():
        return match.group(1).strip() + '\n'
    return f'Release v{version}\n'


def tracked_files(repo_root):
    result = subprocess.run(['git', 'ls-files', '-z'], cwd=repo_root, capture_output=True, check=True)
    return sorted(name for name in result.stdout.decode('utf-8').split('\0') if name)


def build_release(repo_root, output_dir, expected_tag=None):
    repo_root = Path(repo_root).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    version = (repo_root / 'VERSION').read_text(encoding='utf-8').strip()
    version_tuple(version)
    tag = 'v' + version
    if expected_tag and expected_tag != tag:
        raise SystemExit(f'Tag {expected_tag} does not match VERSION {version}')

    archive_name = f'antigravity-suite-{tag}-source.zip'
    archive_path = output_dir / archive_name
    checksum_path = output_dir / (archive_name + '.sha256')
    notes_path = output_dir / 'release-notes.md'

    files = tracked_files(repo_root)
    with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for rel in files:
            safe_source_name(rel)
            src = repo_root / rel
            if src.is_symlink() or not src.is_file():
                raise SystemExit(f'Unsupported tracked entry: {rel}')
            info = zipfile.ZipInfo(f'antigravity-suite/{rel}', date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100755 if src.stat().st_mode & 0o111 else 0o100644) << 16
            bundle.writestr(info, src.read_bytes())

    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    checksum_path.write_text(f'{digest}  {archive_name}\n', encoding='utf-8')
    notes_path.write_text(extract_changelog_notes(repo_root / 'CHANGELOG.md', version), encoding='utf-8')

    # Self-verify against suite_updater's exact extraction & validation rules.
    with tempfile.TemporaryDirectory(prefix='agy-release-verify-') as tmp:
        verified = extract_release(archive_path, Path(tmp) / 'source', version)
        if len(verified) != len(files):
            raise SystemExit('Verified archive file count mismatch')

    return dict(
        version=version,
        tag=tag,
        archive=archive_path,
        checksum=checksum_path,
        notes=notes_path,
        sha256=digest,
        file_count=len(files),
    )


def main():
    parser = argparse.ArgumentParser(description='Build verified Antigravity Suite release archive.')
    parser.add_argument('--output', default='dist', help='Output directory for release artifacts.')
    parser.add_argument('--tag', default=None, help='Optional expected tag (e.g. v0.10.1).')
    args = parser.parse_args()
    info = build_release(ROOT, args.output, expected_tag=args.tag)
    print(f"Built {info['archive'].name} ({info['file_count']} files, sha256={info['sha256']})")


if __name__ == '__main__':
    main()
