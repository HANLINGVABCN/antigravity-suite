import asyncio
import copy
import hashlib
import json
import os
import platform
import struct
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
import pytest

import discovery
from discovery import discover,validate_binary
from process_tracking import ExistingProcess,stop_process,process_ports
from process_supervisor import ProcessSupervisor
from profile_manager import ProfileManager
from server import create_app
from config import atomic_json
from test_suite import config,FakeSupervisor,start,login


def fake_binary(root,arch=None):
    binary=root/'antigravity';binary.parent.mkdir(parents=True,exist_ok=True)
    header=bytearray(64);header[:4]=b'\x7fELF';header[4:6]=bytes([2,1])
    struct.pack_into('<H',header,18,arch or {'aarch64':183,'arm64':183}.get(platform.machine().lower(),62))
    binary.write_bytes(header);binary.chmod(0o755)
    resources=root/'resources';resources.mkdir(exist_ok=True);(resources/'app.asar').write_text('{}')
    return binary


def test_installation_requires_executable_resources_and_matching_architecture(tmp_path):
    binary=fake_binary(tmp_path/'valid')
    assert validate_binary(binary,check_libraries=False)['usable']
    (binary.parent/'resources/app.asar').unlink()
    assert not validate_binary(binary,check_libraries=False)['usable']
    binary=fake_binary(tmp_path/'wrong',arch=40)
    assert not validate_binary(binary,check_libraries=False)['usable']
    missing=tmp_path/'missing'
    assert not validate_binary(missing)['usable']


def test_discovery_finds_existing_configuration_without_copying_credentials(tmp_path,monkeypatch):
    data=tmp_path/'suite-data';data.mkdir()
    binary=fake_binary(tmp_path/'existing/antigravity')
    home=tmp_path/'ordinary-user';native=home/'.config/Antigravity';native.mkdir(parents=True)
    token=home/'.gemini/jetski-standalone-oauth-token';token.parent.mkdir()
    token.write_text(json.dumps({'token':{'refresh_token':'dummy-token'},'id_token_email':'local@example.com'}))
    raw=token.read_bytes();proc=tmp_path/'proc';proc.mkdir()
    original=discovery.validate_binary
    monkeypatch.setattr(discovery,'validate_binary',lambda p:original(p,check_libraries=False))
    cfg=config(data);cfg['antigravity_bin']=str(tmp_path/'missing')
    found=discover(cfg,proc_root=proc,home_candidates=[home],binary_candidates=[binary])
    assert found['binary']==str(binary.resolve()) and len(found['configurations'])==1
    spec=found['configurations'][0]
    manager=ProfileManager(data,data/'profiles');p=manager.register_existing(spec)
    identifier=p['id'];assert p['kind']=='existing' and p['email']=='local@example.com'
    assert manager.home_for(identifier)==home
    assert not (manager.profile_dir(identifier)/'store/jetski-standalone-oauth-token').exists()
    assert token.read_bytes()==raw
    again=manager.register_existing(spec);assert again['id']==identifier and len(manager.list_profiles())==1
    token.write_text(json.dumps({'token':{'refresh_token':'another-dummy'},'id_token_email':'changed@example.com'}))
    manager.refresh_existing_metadata(identifier)
    assert manager.get_profile(identifier)['email']=='changed@example.com' and p['id']==identifier
    manager.update_profile(identifier,name='用户改名');manager.register_existing(spec)
    assert p['name']=='用户改名'
    with pytest.raises(ValueError):manager.delete_profile(identifier)
    assert token.is_file()


def test_existing_restore_never_overwrites_token_with_old_snapshot(tmp_path):
    home=tmp_path/'home';home.mkdir();native=home/'.config/Antigravity';native.mkdir(parents=True)
    manager=ProfileManager(tmp_path/'data',tmp_path/'data/profiles')
    p=manager.register_existing(dict(home=str(home),config_dir=str(native),uid=os.getuid() if hasattr(os,'getuid') else 0,
        binary='/opt/antigravity/antigravity',display=':1',session_env={},available=True))
    old=manager.profile_dir(p['id'])/'store/jetski-standalone-oauth-token';old.write_text('old-snapshot')
    actual=home/'.gemini/jetski-standalone-oauth-token';actual.parent.mkdir(exist_ok=True)
    atomic_json(actual,dict(token=dict(refresh_token='real-login')))
    original=actual.read_bytes()
    manager.restore_profile(p['id'])
    assert actual.read_bytes()==original and manager.home==home
    assert manager.runtime_targets[1][0]==native
    manager.save_active_to_profile(p['id'])
    assert old.read_bytes()==original and actual.read_bytes()==original


@pytest.mark.skipif(os.name=='nt',reason='Linux /proc process tracking')
def test_process_tracking_stops_only_owned_process_and_finds_its_port():
    script="import socket,time; s=socket.socket();s.bind(('127.0.0.1',0));s.listen();print(s.getsockname()[1],flush=True);time.sleep(60)"
    first=subprocess.Popen([sys.executable,'-u','-c',script],stdout=subprocess.PIPE,text=True,start_new_session=True)
    second=subprocess.Popen([sys.executable,'-u','-c',script],stdout=subprocess.PIPE,text=True,start_new_session=True)
    try:
        first_port=int(first.stdout.readline());second_port=int(second.stdout.readline())
        assert first_port in process_ports(first.pid) and second_port not in process_ports(first.pid)
        info=discovery.process_info(first.pid);adopted=ExistingProcess(info)
        assert adopted.poll() is None
        adopted.identity='different-start-time';assert adopted.poll() is not None
        stop_process(first)
        assert first.poll() is not None and second.poll() is None
    finally:
        stop_process(first);stop_process(second)
        first.stdout.close();second.stdout.close()


def test_supervisor_adopts_matching_home_without_launching_duplicate(tmp_path,monkeypatch):
    home=tmp_path/'native';home.mkdir()
    instance=dict(pid=99999,start_time='test',uid=0,executable='/opt/antigravity/antigravity',
        args=['/opt/antigravity/antigravity'],env={'HOME':str(home)},raw='')
    import process_supervisor
    monkeypatch.setattr(process_supervisor,'engine_processes',lambda:[instance])
    s=ProcessSupervisor(antigravity_bin=instance['executable'],runtime_home=home)
    monkeypatch.setattr(s,'detect_port_from_log',lambda:41001)
    monkeypatch.setattr(s,'_start_monitor',lambda:None)
    monkeypatch.setattr(process_supervisor.subprocess,'Popen',lambda *a,**kw:pytest.fail('must not launch duplicate'))
    assert s.adopt_existing() and s.active_port==41001
    assert isinstance(s.process,ExistingProcess) and s.process.pid==99999


@pytest.mark.skipif(os.name=='nt',reason='Linux account and /proc fallback')
def test_desktop_process_identity_is_found_when_environ_and_exe_are_denied(tmp_path,monkeypatch):
    import pwd
    uid=os.getuid();home=pwd.getpwuid(uid).pw_dir
    proc=tmp_path/'123';proc.mkdir()
    executable=fake_binary(tmp_path/'application')
    tail=['S','1']+['0']*17+['42']+['0']*5
    (proc/'stat').write_text('123 (antigravity) '+' '.join(tail))
    (proc/'status').write_text('Uid:\t%d\t%d\t%d\t%d\n'%(uid,uid,uid,uid))
    (proc/'cmdline').write_bytes(str(executable).encode()+b'\0--no-sandbox\0')
    original_read=Path.read_bytes;original_link=os.readlink
    def read(path):
        if path==proc/'environ':raise PermissionError('container restriction')
        return original_read(path)
    def link(path,*args,**kwargs):
        if Path(path)==proc/'exe':raise PermissionError('container restriction')
        return original_link(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_bytes',read);monkeypatch.setattr(os,'readlink',link)
    info=discovery.process_info(123,tmp_path)
    assert info and info['pid']==123 and info['env']['HOME']==home
    assert info['start_time']=='42' and info['executable']==str(executable.resolve())


def test_log_port_from_other_process_is_not_selected(tmp_path,monkeypatch):
    home=tmp_path/'home';log=home/'.config/Antigravity/logs/main.log';log.parent.mkdir(parents=True)
    log.write_text('https://127.0.0.1:49999/\nhttps://127.0.0.1:41001/')
    s=ProcessSupervisor(runtime_home=home)
    class Dummy:
        pid=123
        def poll(self):return None
    s.process=Dummy()
    import process_supervisor
    monkeypatch.setattr(process_supervisor,'process_ports',lambda pid:{49999})
    class Socket:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def settimeout(self,*args):pass
        def connect_ex(self,destination):return 0
    monkeypatch.setattr(process_supervisor.socket,'socket',Socket)
    assert s.detect_port_from_log()==49999
    monkeypatch.setattr(process_supervisor,'process_ports',lambda pid:set())
    assert s.detect_port_from_log() is None


def test_core_button_discovers_installation_without_creating_unlogged_account(tmp_path,monkeypatch):
    async def scenario():
        data=tmp_path/'data';cfg=config(data);cfg['discover_existing']=True
        home=tmp_path/'native';home.mkdir();native=home/'.config/Antigravity';native.mkdir(parents=True)
        spec=dict(home=str(home),config_dir=str(native),uid=os.getuid() if hasattr(os,'getuid') else 0,
            binary='/opt/antigravity/antigravity',display=':1',session_env={},available=True)
        result=dict(binary='/opt/antigravity/antigravity',installations=[dict(path='/opt/antigravity/antigravity',usable=True)],configurations=[spec])
        monkeypatch.setattr(discovery,'discover',lambda cfg:copy.deepcopy(result))
        supervisor=FakeSupervisor()
        supervisor.adopt_existing=lambda:False
        app=create_app(cfg,supervisor=supervisor)
        runner,port=await start(app);url='http://127.0.0.1:%d'%port
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                await login(client,url)
                status=await (await client.get(url+'/__suite/api/system/core-status')).json()
                assert status['installed'] and status['usable'] and status['button_label']=='卸载反重力'
                assert app['state'].profiles.list_profiles()==[]
                installation=app['state'].profiles.native_runtime.installations[0];identifier=installation['id']
                assert installation['native_home']==str(home)
                await app['state'].refresh_existing()
                assert app['state'].profiles.native_runtime.installations[0]['id']==identifier
                assert app['state'].profiles.list_profiles()==[]
                result.update(binary=None,installations=[],configurations=[])
                await app['state'].refresh_existing()
                status=await (await client.get(url+'/__suite/api/system/core-status')).json()
                assert not status['installed'] and status['button_label']=='安装反重力'
                assert app['state'].profiles.native_runtime.installations[0]['available'] is False
        finally:await runner.cleanup()
    asyncio.run(scenario())
