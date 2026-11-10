import json
import os
import sqlite3
import pytest
from pathlib import Path

from config import atomic_json
from external_import import export_external
from profile_manager import ProfileManager


def setup(tmp_path):
    data=tmp_path/'data';home=tmp_path/'native';config=home/'.config/Antigravity'
    config.mkdir(parents=True)
    manager=ProfileManager(data,data/'profiles')
    spec=dict(home=str(home),config_dir=str(config),uid=os.getuid() if hasattr(os,'getuid') else 0,
              binary='/opt/antigravity/antigravity',display=':1',session_env={},available=True)
    manager.native_runtime.refresh_installations([spec])
    return manager,home,spec


def login(home,refresh='dummy-original-refresh',email='example@example.invalid'):
    token=dict(token=dict(refresh_token=refresh,access_token='dummy-access',expiry='2030-01-01T00:00:00Z'),
               id_token_email=email,auth_method='consumer')
    atomic_json(home/'.gemini/jetski-standalone-oauth-token',token)
    return token


def test_initial_existing_login_is_captured_as_an_ordinary_account_without_touching_native_data(tmp_path):
    manager,home,spec=setup(tmp_path);token=login(home)
    config_file=home/'.config/Antigravity/User/settings.json';atomic_json(config_file,dict(theme='original'))
    original_token=(home/'.gemini/jetski-standalone-oauth-token').read_bytes()
    imported=manager.import_installed_accounts_once('测试机器');profiles=manager.list_profiles()
    assert len(imported)==len(profiles)==1
    p=profiles[0]
    assert p['name']=='默认配置-测试机器' and p['email']=='example@example.invalid' and not p.get('kind') and not p.get('internal_runtime')
    assert p['id']==imported[0] and p['id']!=manager.native_runtime.installations[0]['id']
    store=manager.profile_dir(p['id'])/'store'
    assert json.loads((store/'jetski-standalone-oauth-token').read_text())==token
    assert not (store/'Antigravity_config').exists()
    assert json.loads(config_file.read_text())==dict(theme='original')
    assert (home/'.gemini/jetski-standalone-oauth-token').read_bytes()==original_token
    assert manager.home_for(p['id'])==home


def test_unlogged_installation_and_later_refresh_do_not_create_placeholder_or_repeat_import(tmp_path):
    manager,home,spec=setup(tmp_path)
    assert manager.import_installed_accounts_once()==[] and manager.list_profiles()==[]
    login(home)
    manager.native_runtime.refresh_installations([spec])
    assert manager.import_installed_accounts_once()==[] and manager.list_profiles()==[]


def test_default_import_name_uses_the_system_machine_name(tmp_path,monkeypatch):
    manager,home,_=setup(tmp_path);login(home)
    import profile_manager
    monkeypatch.setattr(profile_manager.socket,'gethostname',lambda:'my-vps')
    manager.import_installed_accounts_once()
    assert manager.list_profiles()[0]['name']=='默认配置-my-vps'


def test_existing_ordinary_account_and_legacy_duplicate_keep_ids_binding_and_one_visible_card(tmp_path):
    manager,home,spec=setup(tmp_path);token=login(home)
    ordinary=manager.create_profile('用户自己取的名字','same-proxy-id','example@example.invalid')
    atomic_json(manager.profile_dir(ordinary['id'])/'store/jetski-standalone-oauth-token',token)
    legacy=manager.register_existing(spec)
    manager.native_runtime.binding=dict(profile_id=ordinary['id'],native=manager.native_runtime.installations[0])
    atomic_json(manager.native_runtime.file,manager.native_runtime.binding)
    ids_before=[p['id'] for p in manager.profiles]
    assert manager.import_installed_accounts_once()==[ordinary['id']]
    profiles=manager.list_profiles()
    assert len(profiles)==1 and profiles[0]['id']==ordinary['id']
    assert profiles[0]['name']=='用户自己取的名字' and profiles[0]['proxy_id']=='same-proxy-id'
    assert ids_before==[p['id'] for p in manager.profiles] and manager.get_profile(legacy['id'])['internal_runtime']
    assert manager.native_runtime.resolve(legacy['id'])==ordinary['id']
    reloaded=ProfileManager(manager.data_dir,manager.profiles_dir)
    assert reloaded.import_installed_accounts_once()==[] and len(reloaded.list_profiles())==1


def test_unique_legacy_logged_account_becomes_ordinary_with_the_same_id(tmp_path):
    manager,home,spec=setup(tmp_path);login(home)
    legacy=manager.register_existing(spec);identifier=legacy['id'];created=legacy['created_at']
    manager.import_installed_accounts_once('测试机器')
    p=manager.list_profiles()[0]
    assert p['id']==identifier and p['created_at']==created
    assert p['name']=='默认配置-测试机器' and 'kind' not in p and 'native_home' not in p
    assert manager.native_runtime.spec_for(identifier)['native_home']==str(home)


def test_login_capture_does_not_snapshot_or_touch_desktop_settings_database(tmp_path):
    manager,home,_=setup(tmp_path);login(home)
    path=home/'.config/Antigravity/User/globalStorage/state.vscdb';path.parent.mkdir(parents=True)
    live=sqlite3.connect(path)
    try:
        live.execute('PRAGMA journal_mode=WAL');live.execute('CREATE TABLE settings (name TEXT)')
        live.execute("INSERT INTO settings VALUES ('committed-in-WAL')");live.commit()
        imported=manager.import_installed_accounts_once()
        snapshot=manager.profile_dir(imported[0])/'store/Antigravity_config/User/globalStorage/state.vscdb'
        assert not snapshot.exists()
        assert live.execute('SELECT name FROM settings').fetchone()==('committed-in-WAL',)
    finally:live.close()


def test_deleting_imported_account_does_not_break_restart_or_recreate_it(tmp_path):
    manager,home,_=setup(tmp_path);login(home)
    imported=manager.import_installed_accounts_once();manager.delete_profile(imported[0])
    assert manager.native_runtime.binding is None
    reloaded=ProfileManager(manager.data_dir,manager.profiles_dir)
    assert reloaded.list_profiles()==[] and reloaded.import_installed_accounts_once()==[]
    assert (home/'.gemini/jetski-standalone-oauth-token').is_file()


def test_ordinary_imported_account_exports_current_native_token_after_desktop_refresh(tmp_path):
    manager,home,_=setup(tmp_path);login(home)
    identifier=manager.import_installed_accounts_once()[0]
    login(home,refresh='updated-by-desktop')
    result=export_external(manager,identifier,'cpa')
    assert result['refresh_token']=='updated-by-desktop'


def test_a_different_logged_account_is_not_merged_just_because_a_runtime_is_bound(tmp_path):
    manager,home,_=setup(tmp_path);login(home,email='new@example.invalid')
    old=manager.create_profile('old','keep-proxy','old@example.invalid')
    atomic_json(manager.profile_dir(old['id'])/'store/jetski-standalone-oauth-token',dict(token=dict(refresh_token='another-account')))
    manager.native_runtime.binding=dict(profile_id=old['id'],native=manager.native_runtime.installations[0])
    result=manager.import_installed_accounts_once()
    assert result[0]!=old['id'] and len(manager.list_profiles())==2
    assert manager.get_profile(old['id'])['proxy_id']=='keep-proxy'
    assert manager.get_profile(result[0])['email']=='new@example.invalid'


def test_import_failure_restores_account_metadata_and_runtime_binding(tmp_path,monkeypatch):
    manager,home,_=setup(tmp_path);login(home)
    import profile_manager
    original=profile_manager.atomic_json
    def fail_marker(path,value):
        if Path(path).name=='installed-account-import.json':raise OSError('intentional marker failure')
        return original(path,value)
    monkeypatch.setattr(profile_manager,'atomic_json',fail_marker)
    with pytest.raises(OSError):manager.import_installed_accounts_once()
    assert manager.list_profiles()==[] and manager.native_runtime.binding is None
    reloaded=ProfileManager(manager.data_dir,manager.profiles_dir)
    assert reloaded.list_profiles()==[] and reloaded.native_runtime.binding is None
