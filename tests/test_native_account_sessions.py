import asyncio
import copy
import json
import os
import pytest
from pathlib import Path

from config import atomic_json
from server import create_app
from test_suite import FakeProcess,FakeSupervisor,config


def setup(tmp_path):
    data=tmp_path/'data';home=tmp_path/'native';(home/'.config/Antigravity').mkdir(parents=True)
    cfg=config(data);cfg['discover_existing']=True
    supervisor=FakeSupervisor();supervisor.process=FakeProcess();supervisor.active_port=45000
    app=create_app(cfg,supervisor=supervisor);state=app['state'];manager=state.profiles
    spec=dict(home=str(home),config_dir=str(home/'.config/Antigravity'),uid=os.getuid() if hasattr(os,'getuid') else 0,
              binary='/opt/antigravity/antigravity',display=':1',session_env={},available=True)
    manager.native_runtime.refresh_installations([spec])
    a=manager.create_profile('默认配置-my-vps','node-A','a@example.invalid')
    token=dict(token=dict(refresh_token='saved-A'),id_token_email='a@example.invalid')
    atomic_json(manager.profile_dir(a['id'])/'store/jetski-standalone-oauth-token',token)
    atomic_json(home/'.gemini/jetski-standalone-oauth-token',token)
    manager.native_runtime.associate(a['id'],manager.native_runtime.installations[0]);manager.bind_home(a['id'])
    state.active=state.loaded=a['id'];state.native_runtime_proxy_id='node-A';state.cfg['default_profile_id']=a['id']
    state.request_detection=lambda identifier:None
    return state,a,home


def test_logout_detaches_runtime_and_preserves_saved_account_name_id_credentials_and_binding(tmp_path):
    async def scenario():
        state,a,home=setup(tmp_path);manager=state.profiles
        original=copy.deepcopy(a);store=manager.profile_dir(a['id'])/'store/jetski-standalone-oauth-token';saved=store.read_bytes()
        (home/'.gemini/jetski-standalone-oauth-token').unlink()
        assert manager.save_active_to_profile(a['id']) is False
        await state.sync_native_login(force=True)
        assert state.active is None and state.loaded is None and state.cfg['native_logged_out']
        assert manager.get_profile(a['id'])==original and store.read_bytes()==saved
        assert state.status()['native_session']['state']=='logged_out'
        assert state.cfg['default_profile_id']==a['id']
    asyncio.run(scenario())


def test_login_b_creates_its_own_card_and_never_overwrites_a(tmp_path):
    async def scenario():
        state,a,home=setup(tmp_path);manager=state.profiles
        original=copy.deepcopy(a);store=manager.profile_dir(a['id'])/'store/jetski-standalone-oauth-token';saved=store.read_bytes()
        (home/'.gemini/jetski-standalone-oauth-token').unlink();await state.sync_native_login(force=True)
        atomic_json(home/'.gemini/jetski-standalone-oauth-token',dict(token=dict(refresh_token='new-B'),id_token_email='b@example.invalid'))
        await state.sync_native_login(force=True)
        b=manager.get_profile(state.active)
        assert b['id']!=a['id'] and b['email']=='b@example.invalid' and len(manager.list_profiles())==2
        assert manager.get_profile(a['id'])==original and store.read_bytes()==saved
        await state.sync_native_login(force=True)
        assert len(manager.list_profiles())==2
        assert state.status()['native_session']['state']=='logged_in'
    asyncio.run(scenario())


def test_existing_b_login_creates_a_new_session_and_preserves_b_identity(tmp_path):
    async def scenario():
        state,a,home=setup(tmp_path);manager=state.profiles
        b=manager.create_profile('B自定义名称','node-B','b@example.invalid')
        atomic_json(manager.profile_dir(b['id'])/'store/jetski-standalone-oauth-token',dict(token=dict(refresh_token='old-B'),id_token_email='b@example.invalid'))
        original=copy.deepcopy(b);store=manager.profile_dir(b['id'])/'store/jetski-standalone-oauth-token';saved=store.read_bytes()
        atomic_json(home/'.gemini/jetski-standalone-oauth-token',dict(token=dict(refresh_token='refreshed-B'),id_token_email='b@example.invalid'))
        await state.sync_native_login(force=True)
        assert state.active not in (a['id'],b['id']) and len(manager.list_profiles())==3
        assert b==original and store.read_bytes()==saved
        assert b['name']=='B自定义名称' and b['proxy_id']=='node-B' and a['email']=='a@example.invalid'
        assert manager.get_profile(state.active)['proxy_id']=='node-A'
    asyncio.run(scenario())


def test_different_native_login_cannot_be_written_into_as_snapshot_even_before_scan(tmp_path):
    async def scenario():
        state,a,home=setup(tmp_path);manager=state.profiles
        store=manager.profile_dir(a['id'])/'store/jetski-standalone-oauth-token';saved=store.read_bytes()
        atomic_json(home/'.gemini/jetski-standalone-oauth-token',dict(token=dict(refresh_token='B'),id_token_email='b@example.invalid'))
        assert manager.save_active_to_profile(a['id']) is False and store.read_bytes()==saved
    asyncio.run(scenario())


def test_detached_runtime_cannot_overwrite_a_with_bs_credentials(tmp_path):
    async def scenario():
        state,a,home=setup(tmp_path);manager=state.profiles
        store=manager.profile_dir(a['id'])/'store/jetski-standalone-oauth-token';saved=store.read_bytes()
        manager.native_runtime.detach()
        atomic_json(home/'.gemini/jetski-standalone-oauth-token',dict(token=dict(refresh_token='B'),id_token_email='b@example.invalid'))
        assert manager.save_active_to_profile(a['id']) is False and store.read_bytes()==saved
    asyncio.run(scenario())


def test_login_change_must_be_stable_before_background_association(tmp_path):
    async def scenario():
        state,a,home=setup(tmp_path)
        atomic_json(home/'.gemini/jetski-standalone-oauth-token',dict(token=dict(refresh_token='B'),id_token_email='b@example.invalid'))
        await state.sync_native_login()
        assert state.active==a['id'] and len(state.profiles.list_profiles())==1
        await state.sync_native_login()
        assert state.active!=a['id'] and len(state.profiles.list_profiles())==2
    asyncio.run(scenario())


def test_update_restart_defers_new_account_creation_until_update_finishes(tmp_path):
    async def scenario():
        state,a,home=setup(tmp_path)
        atomic_json(home/'.gemini/jetski-standalone-oauth-token',dict(token=dict(refresh_token='B'),id_token_email='b@example.invalid'))
        atomic_json(state.updater.status_file,dict(phase='restarting'))
        await state.sync_native_login(force=True)
        assert state.active==a['id'] and len(state.profiles.list_profiles())==1
        atomic_json(state.updater.status_file,dict(phase='success'))
        await state.sync_native_login(force=True)
        assert state.active!=a['id'] and len(state.profiles.list_profiles())==2
    asyncio.run(scenario())


def test_failed_b_snapshot_keeps_a_and_does_not_leave_a_placeholder_b_card(tmp_path,monkeypatch):
    async def scenario():
        state,a,home=setup(tmp_path);before=copy.deepcopy(a)
        atomic_json(home/'.gemini/jetski-standalone-oauth-token',dict(token=dict(refresh_token='B'),id_token_email='b@example.invalid'))
        def fail(*args,**kwargs):
            assert state.profiles.list_profiles()==[before]
            raise OSError('intentional capture failure')
        monkeypatch.setattr(state.profiles,'capture_installed_account',fail)
        with pytest.raises(OSError):await state.sync_native_login(force=True)
        assert state.profiles.list_profiles()==[before] and state.active==a['id']
    asyncio.run(scenario())


def test_existing_account_can_be_started_to_apply_its_saved_proxy(tmp_path):
    async def scenario():
        state,a,home=setup(tmp_path);state.native_requires_restart=True
        calls=[]
        async def activate(identifier):calls.append(identifier)
        state.activate=activate
        state.request_start(a['id'])
        assert state.launch_task is not None
        await state.launch_task
        assert calls==[a['id']]
    asyncio.run(scenario())


def test_deleting_a_card_does_not_recreate_it_from_unchanged_native_login(tmp_path):
    async def scenario():
        state,a,home=setup(tmp_path)
        state.profiles.delete_profile(a['id']);state.active=state.loaded=None
        await state.sync_native_login(force=True)
        assert state.profiles.list_profiles()==[]
        atomic_json(home/'.gemini/jetski-standalone-oauth-token',dict(token=dict(refresh_token='B'),id_token_email='b@example.invalid'))
        await state.sync_native_login(force=True)
        assert len(state.profiles.list_profiles())==1 and state.profiles.list_profiles()[0]['email']=='b@example.invalid'
    asyncio.run(scenario())
