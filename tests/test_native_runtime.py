import json
import os
from pathlib import Path

import pytest
from config import atomic_json
from profile_manager import ProfileManager


def fixture(tmp_path,monkeypatch):
    manager=ProfileManager(tmp_path/'data',tmp_path/'data/profiles')
    monkeypatch.setattr(manager.history,'attach',lambda home:None)
    home=tmp_path/'native';config=home/'.config/Antigravity';config.mkdir(parents=True)
    (home/'.config/OtherApplication').mkdir();(home/'.config/OtherApplication/untouched').write_text('keep')
    spec=manager.register_existing(dict(home=str(home),config_dir=str(config),uid=os.getuid() if hasattr(os,'getuid') else 0,
        binary='/opt/antigravity/antigravity',display=':1',session_env={},available=True))
    manager.native_runtime.select(manager.list_profiles())
    return manager,home,spec


def account(manager,name):
    profile=manager.create_profile(name,'fixed-node-binding',name+'@example.invalid')
    store=manager.profile_dir(profile['id'])/'store'
    atomic_json(store/'jetski-standalone-oauth-token',dict(token=dict(refresh_token='dummy-'+name),id_token_email=name+'@example.invalid'))
    atomic_json(store/'Antigravity_config/User/settings.json',dict(account=name))
    return profile


def test_desktop_and_web_share_selected_login_but_account_archives_and_ids_are_kept(tmp_path,monkeypatch):
    manager,home,existing=fixture(tmp_path,monkeypatch)
    native_token=home/'.gemini/jetski-standalone-oauth-token'
    atomic_json(native_token,dict(token=dict(refresh_token='original-desktop')))
    a=account(manager,'A');b=account(manager,'B')
    assert manager.native_runtime.activate(a['id'])
    manager.restore_profile(a['id'])
    assert manager.home_for(a['id'])==home
    assert manager.runtime_targets[1][0]==home/'.config/Antigravity'
    assert json.loads(native_token.read_text())['token']['refresh_token']=='dummy-A'
    assert manager.native_runtime.resolve(existing['id'])==a['id']
    # Simulates a refresh/login performed by the actual desktop app.
    atomic_json(native_token,dict(token=dict(refresh_token='desktop-refreshed-A'),id_token_email='A@example.invalid'))
    manager.save_active_to_profile(a['id'])
    assert json.loads((manager.profile_dir(a['id'])/'home/.gemini/jetski-standalone-oauth-token').read_text())['token']['refresh_token']=='desktop-refreshed-A'
    manager.native_runtime.activate(b['id']);manager.restore_profile(b['id'])
    assert json.loads(native_token.read_text())['token']['refresh_token']=='dummy-B'
    manager.save_active_to_profile(b['id'])
    manager.native_runtime.activate(a['id']);manager.restore_profile(a['id'])
    assert json.loads(native_token.read_text())['token']['refresh_token']=='desktop-refreshed-A'
    assert len(manager.list_profiles())==3 and a['proxy_id']==b['proxy_id']=='fixed-node-binding'
    assert (home/'.config/OtherApplication/untouched').read_text()=='keep'
    backup=Path(manager.native_runtime.binding['backup'])
    assert backup.is_dir()


def test_restart_uses_native_updated_config_and_never_restores_an_old_snapshot(tmp_path,monkeypatch):
    manager,home,existing=fixture(tmp_path,monkeypatch)
    a=account(manager,'A');manager.native_runtime.activate(a['id']);manager.restore_profile(a['id'])
    token=home/'.gemini/jetski-standalone-oauth-token'
    atomic_json(token,dict(token=dict(refresh_token='new-desktop-login'),id_token_email='A@example.invalid'))
    reloaded=ProfileManager(manager.data_dir,manager.profiles_dir)
    reloaded.native_runtime.select(reloaded.list_profiles())
    assert reloaded.native_runtime.activate(a['id'])
    reloaded.restore_profile(a['id'])
    assert json.loads(token.read_text())['token']['refresh_token']=='new-desktop-login'
    assert reloaded.native_runtime.status()['existing_profile_id']==existing['id']


def test_switch_failure_restores_original_native_credentials(tmp_path,monkeypatch):
    manager,home,_=fixture(tmp_path,monkeypatch)
    token=home/'.gemini/jetski-standalone-oauth-token'
    atomic_json(token,dict(token=dict(refresh_token='original')))
    a=account(manager,'A')
    def fail(*args,**kwargs):raise OSError('intentional filesystem failure')
    monkeypatch.setattr(manager.history,'sync_native',fail)
    with pytest.raises(OSError):manager.native_runtime.activate(a['id'])
    assert json.loads(token.read_text())['token']['refresh_token']=='original'
    assert manager.native_runtime.binding is None


def test_multiple_existing_environments_require_unambiguous_original_selection(tmp_path,monkeypatch):
    manager,home,_=fixture(tmp_path,monkeypatch)
    other=tmp_path/'other';(other/'.config/Antigravity').mkdir(parents=True)
    manager.register_existing(dict(home=str(other),config_dir=str(other/'.config/Antigravity'),uid=0,
        binary='/opt/antigravity/antigravity',display=':2',session_env={},available=True))
    assert manager.native_runtime.select(manager.list_profiles()) is None
    a=account(manager,'A');assert manager.native_runtime.activate(a['id']) is False
