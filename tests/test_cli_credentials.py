import json
import os
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

import pytest

from config import atomic_json
from profile_manager import ProfileManager


@pytest.fixture
def shared(tmp_path, monkeypatch):
    if os.name != 'posix':
        pytest.skip('Linux CLI file-storage integration')
    manager = ProfileManager(tmp_path / 'data', tmp_path / 'data/profiles')
    monkeypatch.setattr(manager.history, 'attach', lambda home: None)
    monkeypatch.setattr(manager.history, 'sync_native', lambda *args: None)
    home = tmp_path / 'native'
    config = home / '.config/Antigravity'
    config.mkdir(parents=True)
    binary = home / '.local/bin/agy'
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'\x7fELF CLITokenFilePath cliFileTokenStorage')
    spec = manager.native_runtime.register_installation(dict(home=str(home), config_dir=str(config),
        uid=os.getuid(), binary='/opt/antigravity/antigravity', display=':1', available=True))
    manager.native_runtime.select()
    return manager, spec, home


def token(grant='saved-A', access='access-old', expiry='2026-11-08T10:00:00+08:00'):
    return dict(token=dict(refresh_token=grant, access_token=access, expiry=expiry),
                auth_method='consumer', id_token_email='account@example.invalid', writer_id='antigravity')


def test_cli_renewal_and_native_atomic_switch_use_one_file_without_touching_history(shared):
    manager, spec, home = shared
    native, alias = manager.cli_credentials.paths(spec)
    atomic_json(native, token())
    history = home / '.gemini/antigravity/conversations/keep.pb'
    history.parent.mkdir(parents=True)
    history.write_bytes(b'original-conversation')
    assert manager.cli_credentials.ensure(spec)
    assert alias.is_symlink() and alias.resolve() == native
    with alias.open('w') as stream:
        json.dump(token(access='cli-renewed'), stream)
    assert json.loads(native.read_text())['token']['access_token'] == 'cli-renewed'
    atomic_json(native, token(grant='saved-B'))
    assert json.loads(alias.read_text())['token']['refresh_token'] == 'saved-B'
    assert history.read_bytes() == b'original-conversation'
    assert native.stat().st_mode & 0o777 == 0o600


def test_existing_cli_refresh_is_preserved_and_original_files_are_backed_up(shared):
    manager, spec, _ = shared
    native, alias = manager.cli_credentials.paths(spec)
    atomic_json(native, token())
    atomic_json(alias, token(access='fresh-cli', expiry='2026-11-08T20:00:00+08:00'))
    assert manager.cli_credentials.ensure(spec)
    assert json.loads(native.read_text())['token']['access_token'] == 'fresh-cli'
    assert not (manager.data_dir / 'cli-credential-backups').exists()


def test_other_cli_account_is_not_silently_overwritten_but_explicit_switch_unifies_it(shared):
    manager, spec, _ = shared
    native, alias = manager.cli_credentials.paths(spec)
    atomic_json(native, token())
    atomic_json(alias, token(grant='another-login'))
    with pytest.raises(ValueError, match='CLI 与桌面当前登录不同'):
        manager.cli_credentials.ensure(spec)
    assert json.loads(alias.read_text())['token']['refresh_token'] == 'another-login'
    manager.cli_credentials.ensure(spec, prefer_native=True)
    assert alias.is_symlink() and json.loads(alias.read_text())['token']['refresh_token'] == 'saved-A'
    archived = next(p for p in manager.list_profiles() if p['name'].startswith('CLI 登录-'))
    credential = manager.profile_dir(archived['id'])/'store/jetski-standalone-oauth-token'
    assert json.loads(credential.read_text())['token']['refresh_token'] == 'another-login'
    assert not (manager.data_dir / 'cli-credential-backups').exists()


def test_cli_only_login_can_be_imported_and_empty_shared_link_does_not_restore_login(shared):
    manager, spec, _ = shared
    native, alias = manager.cli_credentials.paths(spec)
    atomic_json(alias, token())
    manager.cli_credentials.ensure(spec)
    assert json.loads(native.read_text())['token']['refresh_token'] == 'saved-A'
    native.unlink()
    manager.cli_credentials.ensure(spec)
    assert not native.exists() and not alias.exists() and alias.is_symlink()
    assert manager.native_runtime.observe_login(force=True)['state'] == 'logged_out'


def test_partial_or_unsupported_cli_credentials_do_not_replace_native_login(shared):
    manager, spec, _ = shared
    native, alias = manager.cli_credentials.paths(spec)
    atomic_json(native, token())
    alias.parent.mkdir(parents=True)
    alias.write_text('{')
    with pytest.raises(ValueError, match='不是兼容'):
        manager.cli_credentials.ensure(spec)
    assert json.loads(native.read_text())['token']['refresh_token'] == 'saved-A'
    native.write_text('{')
    assert manager.cli_credentials.ensure(spec) is False
    assert alias.read_text() == '{'


def test_cli_path_cannot_link_or_write_outside_runtime_home(shared, tmp_path):
    manager, spec, home = shared
    outside = tmp_path / 'other-app'
    outside.mkdir()
    marker = outside / 'antigravity-oauth-token'
    marker.write_text('untouched')
    (home / '.gemini').mkdir()
    (home / '.gemini/antigravity-cli').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='不能指向'):
        manager.cli_credentials.ensure(spec)
    assert marker.read_text() == 'untouched'


def test_no_cli_installation_leaves_runtime_and_credentials_untouched(shared):
    manager, spec, home = shared
    (home / '.local/bin/agy').unlink()
    assert not manager.cli_credentials.ensure(spec)
    assert not (home / '.gemini').exists()


def test_profile_switch_keeps_ids_and_archive_credentials_while_cli_follows_selected_account(shared):
    manager, spec, home = shared
    ids = []
    for grant in ('saved-A', 'saved-B'):
        profile = manager.create_profile(grant, proxy_id='unchanged-node')
        atomic_json(manager.profile_dir(profile['id']) / 'store/jetski-standalone-oauth-token', token(grant=grant))
        ids.append(profile['id'])
    native, alias = manager.cli_credentials.paths(spec)
    manager.native_runtime.activate(ids[0])
    assert json.loads(alias.read_text())['token']['refresh_token'] == 'saved-A'
    manager.native_runtime.activate(ids[1])
    assert json.loads(alias.read_text())['token']['refresh_token'] == 'saved-B'
    assert [p['id'] for p in manager.list_profiles()] == ids
    assert all(p['proxy_id'] == 'unchanged-node' for p in manager.list_profiles())
    assert json.loads((manager.profile_dir(ids[0]) / 'store/jetski-standalone-oauth-token').read_text())['token']['refresh_token'] == 'saved-A'


@pytest.mark.parametrize('pidfd', [True, False])
def test_running_cli_is_closed_automatically_without_closing_another_home(shared, monkeypatch, tmp_path, pidfd):
    manager, spec, home = shared
    if not pidfd:
        monkeypatch.delattr(os, 'pidfd_open', raising=False)
    monkeypatch.setattr(manager.cli_credentials, 'binary', lambda home: Path(sys.executable).resolve())
    child = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(20)'],
                             env=dict(os.environ, HOME=str(home)))
    other_child = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(20)'],
                                   env=dict(os.environ, HOME=str(tmp_path / 'other-home')))
    try:
        assert manager.cli_credentials.stop(spec) == 1
        assert child.wait(timeout=5) != 0
        assert other_child.poll() is None
        assert manager.cli_credentials.stop(spec) == 0
    finally:
        for process in (child, other_child):
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)


def test_root_service_closes_cli_as_native_user_without_reading_other_user_proc(shared, monkeypatch):
    if os.geteuid() != 0:
        pytest.skip('Requires root to exercise native UID delegation')
    import pwd
    manager, spec, _ = shared
    user = pwd.getpwnam('nobody')
    home = Path(tempfile.mkdtemp(prefix='cli-owner-test-'))
    os.chown(home, user.pw_uid, user.pw_gid)
    python = Path(getattr(sys, '_base_executable', sys.executable)).resolve()
    monkeypatch.setattr(manager.cli_credentials, 'binary', lambda home: python)
    spec = dict(spec, native_home=str(home), native_uid=user.pw_uid)
    child = subprocess.Popen([str(python), '-B', '-c', 'import time; time.sleep(20)'],
                             user=user.pw_uid, group=user.pw_gid, cwd=home,
                             env=dict(os.environ, HOME=str(home)))
    try:
        assert manager.cli_credentials.stop(spec) == 1
        assert child.wait(timeout=5) != 0
        assert manager.cli_credentials.stop(spec) == 0
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5)
        assert home.parent == Path(tempfile.gettempdir()) and home.name.startswith('cli-owner-test-')
        shutil.rmtree(home)
