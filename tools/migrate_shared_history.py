"""Back up and migrate a stopped, private legacy instance to shared conversations."""
import argparse
import json
import os
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import atomic_json, DATA_DIR
from profile_manager import ProfileManager


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=DATA_DIR)
    args = parser.parse_args()
    data = args.data_dir.resolve()
    config_path = data / 'config.json'
    if not config_path.is_file():
        parser.error('Data directory must contain an existing config.json')
    cfg = json.loads(config_path.read_text(encoding='utf-8'))
    profiles_dir = Path(cfg.get('profiles_dir', data / 'profiles')).resolve()
    legacy = Path(cfg.get('runtime_home', data / 'runtime-home')).resolve()
    # A migration must never race a running engine or database writer.
    if Path('/proc').is_dir():
        references = [str(data), str(profiles_dir), str(legacy)]
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit() or int(entry.name) == os.getpid():
                continue
            try:
                command = (entry / 'cmdline').read_bytes().decode(errors='replace')
            except OSError:
                continue
            if 'antigravity' in command.lower() and any(path in command for path in references):
                parser.error('Stop the suite and its engines before migration (PID %s)' % entry.name)
    backup = data.parent / 'antigravity-suite-backups'
    backup.mkdir(parents=True, exist_ok=True, mode=0o700)
    archive = backup / ('before-shared-history-%d.tar.gz' % time.time_ns())
    def regular_only(info):
        return info if info.isfile() or info.isdir() or info.issym() else None
    with tarfile.open(archive, 'w:gz', dereference=False) as tar:
        tar.add(data, arcname=data.name, filter=regular_only)
        if not profiles_dir.is_relative_to(data) and profiles_dir.is_dir():
            tar.add(profiles_dir, arcname='legacy-profiles', filter=regular_only)
        if not legacy.is_relative_to(data) and legacy.is_dir():
            tar.add(legacy, arcname='legacy-runtime', filter=regular_only)
    os.chmod(archive, 0o600)
    manager = ProfileManager(data, profiles_dir, legacy)
    sources = [legacy / '.gemini/antigravity']
    for profile in manager.list_profiles():
        folder = manager.profile_dir(profile['id'])
        sources.extend([folder / 'store/gemini_antigravity', folder / 'home/.gemini/antigravity'])
    for source in sources:
        if source.exists():
            manager.history.merge_native(source)
    import shutil
    active = cfg.get('active_profile_id')
    if manager.get_profile(active) and legacy.is_dir():
        home = manager.home_for(active)
        if not home.exists():
            shutil.copytree(legacy, home, ignore=shutil.ignore_patterns('Singleton*', '.tmp', 'engine.log'))
    for profile in manager.list_profiles():
        home = manager.home_for(profile['id'])
        if home.is_dir():
            manager.history.attach(home)
            (home / '.suite-home-initialized').write_text('1', encoding='ascii')
    cfg['profiles_dir'] = str(profiles_dir)
    cfg['shared_history_migration'] = True
    atomic_json(config_path, cfg)
    print('Backup saved: %s' % archive)
    print('Shared conversation library prepared for %d profiles' % len(manager.list_profiles()))


if __name__ == '__main__':
    main()
