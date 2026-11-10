import json
import os
import sqlite3
import hashlib
from pathlib import Path

import pytest

from config import atomic_json
from profile_manager import ProfileManager
from shared_history import DATABASE

def files(root):
    return {str(p.relative_to(root)):(p.stat().st_ino,p.read_bytes()) for p in root.rglob('*') if p.is_file() and not p.name.endswith(('-wal','-shm'))}

@pytest.fixture
def native_profile(tmp_path):
    data=tmp_path/'data';home=tmp_path/'native';(home/'.config/Antigravity').mkdir(parents=True)
    manager=ProfileManager(data,data/'profiles')
    manager.native_runtime.refresh_installations([dict(home=str(home),config_dir=str(home/'.config/Antigravity'),
        uid=os.getuid() if hasattr(os,'getuid') else 0,binary='/opt/antigravity/antigravity',display=':1',available=True)])
    profile=manager.create_profile('current',proxy_id='same-node',email='a@example.invalid')
    token=dict(token=dict(refresh_token='dummy-original'),id_token_email=profile['email'])
    atomic_json(home/'.gemini/jetski-standalone-oauth-token',token)
    atomic_json(manager.profile_dir(profile['id'])/'store/jetski-standalone-oauth-token',token)
    manager.native_runtime.associate(profile['id'],manager.native_runtime.selected);manager.bind_home(profile['id'])
    native=home/'.gemini/antigravity'
    (native/'conversations').mkdir(parents=True,exist_ok=True);(native/'brain/session').mkdir(parents=True,exist_ok=True)
    (native/'conversations/chat.pb').write_bytes(b'original-conversation')
    (native/'brain/session/notes.md').write_text('original-note')
    (native/'preferences.json').write_text('shared preference')
    atomic_json(home/'.config/Antigravity/User/settings.json',dict(theme='unchanged',microphone='unchanged'))
    with sqlite3.connect(native/DATABASE) as db:
        db.execute('CREATE TABLE conversation_summaries(conversation_id TEXT PRIMARY KEY,last_modified_time INTEGER)')
        db.execute('INSERT INTO conversation_summaries VALUES (?,?)',('original-chat',1))
    db.close()
    return manager,profile,native

def test_ten_account_switches_and_saves_do_not_touch_history_settings_or_archive_directories(native_profile,monkeypatch):
    manager,first,native=native_profile
    home=native.parent.parent
    second=manager.create_profile('second',email='b@example.invalid')
    atomic_json(manager.profile_dir(second['id'])/'store/jetski-standalone-oauth-token',
                dict(token=dict(refresh_token='dummy-B'),id_token_email=second['email']))
    before=files(native);settings=files(home/'.config/Antigravity')
    identities={p['id']:manager.profile_dir(p['id']).stat().st_ino for p in [first,second]}
    def forbidden(*args,**kwargs):raise AssertionError('Routine account operation touched the chat library')
    for method in ['merge_native','sync_native','attach']:
        monkeypatch.setattr(manager.history,method,forbidden)
    for _ in range(5):
        for profile in [second,first]:
            manager.native_runtime.activate(profile['id']);manager.bind_home(profile['id'])
            assert manager.home_for(profile['id'])==home
            assert manager.save_active_to_profile(profile['id'])
            assert manager.profile_dir(profile['id']).stat().st_ino==identities[profile['id']]
            assert not (manager.profile_dir(profile['id'])/'home').exists()
            assert files(native)==before and files(home/'.config/Antigravity')==settings
    assert not (manager.data_dir/'native-runtime-backups').exists()
    assert not (manager.data_dir/'shared-history/migration-backups').exists()

def test_access_token_renewal_updates_only_the_selected_archive(native_profile):
    manager,profile,native=native_profile
    before=files(native)
    token=native.parent/'jetski-standalone-oauth-token'
    atomic_json(token,dict(token=dict(refresh_token='dummy-original',access_token='renewed'),id_token_email=profile['email']))
    metadata=manager.installed_token(token)
    manager.renew_session_token(profile['id'],manager.native_runtime.selected,metadata)
    saved=manager.profile_dir(profile['id'])/'store/jetski-standalone-oauth-token'
    assert json.loads(saved.read_text())['token']['access_token']=='renewed'
    assert files(native)==before
    assert list((manager.data_dir/'profiles').iterdir())==[manager.profile_dir(profile['id'])]

def test_one_time_upgrade_merges_old_chat_copies_and_keeps_original_archives(native_profile):
    manager,profile,native=native_profile
    store=manager.profile_dir(profile['id'])/'store/gemini_antigravity/conversations'
    store.mkdir(parents=True);(store/'unique-old.pb').write_bytes(b'unique-old')
    old_files=files(manager.profile_dir(profile['id']))
    assert manager.migrate_account_layout_once(manager.native_runtime.selected)
    assert manager.history.root==native
    assert (native/'conversations/unique-old.pb').read_bytes()==b'unique-old'
    assert files(manager.profile_dir(profile['id']))==old_files
    assert not manager.migrate_account_layout_once(manager.native_runtime.selected)
    assert not (manager.data_dir/'shared-history/migration-backups').exists()

def test_first_upgrade_includes_committed_history_wal(native_profile):
    manager,profile,native=native_profile
    old=manager.history.legacy_root/DATABASE
    with sqlite3.connect(old) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE conversation_summaries(conversation_id TEXT PRIMARY KEY,last_modified_time INTEGER)')
        db.execute('INSERT INTO conversation_summaries VALUES (?,?)',('wal-old-chat',2));db.commit()
        assert manager.migrate_account_layout_once(manager.native_runtime.selected)
    with sqlite3.connect(native/DATABASE) as db:
        assert set(db.execute('SELECT conversation_id FROM conversation_summaries'))=={('original-chat',),('wal-old-chat',)}

def test_switch_binding_failure_restores_credentials_without_new_backups(native_profile,monkeypatch):
    manager,profile,native=native_profile
    other=manager.create_profile('other',email='b@example.invalid')
    atomic_json(manager.profile_dir(other['id'])/'store/jetski-standalone-oauth-token',
                dict(token=dict(refresh_token='dummy-B'),id_token_email=other['email']))
    token=native.parent/'jetski-standalone-oauth-token';before=token.read_bytes();history=files(native)
    def fail(*args):raise OSError('binding write failed')
    monkeypatch.setattr(manager.native_runtime,'associate',fail)
    with pytest.raises(OSError):manager.native_runtime.activate(other['id'])
    assert token.read_bytes()==before and manager.native_runtime.binding['profile_id']==profile['id']
    assert files(native)==history
    assert not (manager.data_dir/'native-runtime-backups').exists()
