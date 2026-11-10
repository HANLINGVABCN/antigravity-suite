import json
import os
import sqlite3
from pathlib import Path

import pytest

from config import atomic_json
from profile_manager import ProfileManager
from shared_history import DATABASE,DIRECTORIES


pytestmark=pytest.mark.skipif(os.name=='nt',reason='Shared history uses Linux profile symlinks')


@pytest.fixture
def native_profile(tmp_path):
    data=tmp_path/'data';home=tmp_path/'native';(home/'.config/Antigravity').mkdir(parents=True)
    manager=ProfileManager(data,data/'profiles')
    spec=manager.native_runtime.register_installation(dict(home=str(home),config_dir=str(home/'.config/Antigravity'),
        uid=os.getuid(),binary='/opt/antigravity/antigravity',display=':1',available=True))
    profile=manager.create_profile('current',proxy_id='same-node',email='a@example.invalid')
    value=dict(token=dict(refresh_token='dummy-original'),id_token_email=profile['email'])
    atomic_json(home/'.gemini/jetski-standalone-oauth-token',value)
    manager.native_runtime.associate(profile['id'],spec);manager.bind_home(profile['id'])
    native=home/'.gemini/antigravity'
    for name in DIRECTORIES:(native/name).mkdir(parents=True)
    (native/'conversations/chat.pb').write_bytes(b'original-conversation')
    (native/'brain/session').mkdir();(native/'brain/session/notes.md').write_text('original-note')
    (native/'preferences.json').write_text('{"setting":"original"}')
    with sqlite3.connect(native/DATABASE) as db:
        db.execute('CREATE TABLE conversation_summaries(conversation_id TEXT PRIMARY KEY,last_modified_time INTEGER)')
        db.execute('INSERT INTO conversation_summaries VALUES (?,?)',('original-chat',1))
    return manager,profile,native


def backups(manager):
    path=manager.history.root/'migration-backups'
    return sorted(p.relative_to(path) for p in path.rglob('*')) if path.exists() else []


def test_repeated_native_saves_keep_shared_links_without_repeating_history_migration(native_profile):
    manager,profile,native=native_profile
    private=manager.profile_dir(profile['id'])/'home/.gemini/antigravity'
    for n in range(4):
        (native/'preferences.json').write_text(json.dumps(dict(setting=n)))
        assert manager.save_active_to_profile(profile['id'])
        assert backups(manager)==[]
        for name in (*DIRECTORIES,DATABASE):
            assert (private/name).is_symlink()
            assert (private/name).resolve()==manager.history.root/name
        assert json.loads((private/'preferences.json').read_text())['setting']==n
        assert (private/'conversations/chat.pb').read_bytes()==b'original-conversation'
        assert (private/'brain/session/notes.md').read_text()=='original-note'
        assert manager.list_profiles()[0]['proxy_id']=='same-node'
    assert manager.list_profiles()[0]['id']==profile['id']
    assert (native/'conversations').is_dir() and not (native/'conversations').is_symlink()
    with sqlite3.connect(private/DATABASE) as db:
        assert db.execute('SELECT conversation_id FROM conversation_summaries').fetchall()==[('original-chat',)]


def test_preexisting_private_history_is_merged_before_metadata_snapshot(native_profile):
    manager,profile,native=native_profile
    private=manager.profile_dir(profile['id'])/'home/.gemini/antigravity'
    (private/'conversations').mkdir(parents=True)
    (private/'conversations/private-only.pb').write_bytes(b'unique-private-history')
    manager.native_runtime.snapshot_to_private(profile['id'])
    assert (manager.history.root/'conversations/private-only.pb').read_bytes()==b'unique-private-history'
    assert (private/'conversations/private-only.pb').read_bytes()==b'unique-private-history'
    assert backups(manager)==[]


def test_committed_sqlite_wal_is_preserved_without_archiving_transient_sidecars(native_profile):
    manager,profile,native=native_profile
    with sqlite3.connect(native/DATABASE) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('INSERT INTO conversation_summaries VALUES (?,?)',('wal-chat',2));db.commit()
        assert (native/(DATABASE+'-wal')).exists()
        manager.native_runtime.snapshot_to_private(profile['id'])
        private=manager.profile_dir(profile['id'])/'home/.gemini/antigravity'
        with sqlite3.connect(private/DATABASE) as snapshot:
            assert set(snapshot.execute('SELECT conversation_id FROM conversation_summaries'))=={('original-chat',),('wal-chat',)}
        assert not (private/(DATABASE+'-wal')).exists()
        assert not (private/(DATABASE+'-shm')).exists()
        assert backups(manager)==[]


def test_failed_metadata_copy_keeps_the_existing_shared_links(native_profile,monkeypatch):
    manager,profile,native=native_profile
    manager.native_runtime.snapshot_to_private(profile['id'])
    private=manager.profile_dir(profile['id'])/'home/.gemini/antigravity'
    before=(private/'preferences.json').read_bytes()
    from profile_manager import safe_copytree
    def fail(src,dest,**kwargs):
        if Path(src)==native:raise OSError('copy unavailable')
        return safe_copytree(src,dest,**kwargs)
    monkeypatch.setattr('profile_manager.safe_copytree',fail)
    with pytest.raises(OSError,match='copy unavailable'):
        manager.native_runtime.snapshot_to_private(profile['id'])
    assert (private/'preferences.json').read_bytes()==before
    assert (private/'conversations').is_symlink()
    assert (private/'conversations/chat.pb').read_bytes()==b'original-conversation'
    assert backups(manager)==[]


def test_one_time_migration_keeps_its_original_backup_and_future_saves_do_not_add_more(native_profile):
    manager,profile,native=native_profile
    private_home=manager.profile_dir(profile['id'])/'home'
    private=private_home/'.gemini/antigravity'
    (private/'conversations').mkdir(parents=True)
    (private/'conversations/legacy.pb').write_bytes(b'original-legacy-data')
    manager.history.attach(private_home)
    original=backups(manager)
    assert original
    for _ in range(3):manager.native_runtime.snapshot_to_private(profile['id'])
    assert backups(manager)==original
    assert (private/'conversations/legacy.pb').read_bytes()==b'original-legacy-data'
