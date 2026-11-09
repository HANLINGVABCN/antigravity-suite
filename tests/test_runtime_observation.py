import asyncio
import copy
import json
import os
import subprocess
from pathlib import Path

import aiohttp
import pytest

import process_supervisor
from process_supervisor import ProcessSupervisor
from server import create_app
from test_suite import config,FakeSupervisor,FakeProcess,start,login


def test_exited_process_is_observed_without_automatic_restart(tmp_path,monkeypatch):
    supervisor=ProcessSupervisor(runtime_home=tmp_path)
    class Exited:
        def poll(self):return 0
    supervisor.process=Exited();supervisor.active_port=45000
    monkeypatch.setattr(supervisor,'find_existing',lambda:None)
    monkeypatch.setattr(supervisor,'start_antigravity',lambda *args:pytest.fail('manual close must not restart'))
    monkeypatch.setattr(process_supervisor.time,'sleep',lambda *args:setattr(supervisor,'_running',False))
    supervisor._monitor_loop()
    assert supervisor.process is None and supervisor.active_port is None and supervisor.exit_sequence==1


def test_manual_open_adopts_only_exact_binary_home_user_and_config(tmp_path,monkeypatch):
    home=tmp_path/'home';home.mkdir()
    native_binary=tmp_path/'native/antigravity'
    native=dict(pid=99999,start_time='test',uid=1234,executable=str(native_binary),
                args=[str(native_binary)],env={'HOME':str(home),'HTTPS_PROXY':'http://127.0.0.1:12345'},raw='')
    candidates=[]
    for change in [dict(executable=str(tmp_path/'other/antigravity')),dict(uid=4321),
                   dict(env={'HOME':str(tmp_path/'another-home')}),dict(args=[str(native_binary),'--user-data-dir='+str(home/'wrong-config')])]:
        candidates.append(dict(native,**change))
    candidates.append(native)
    supervisor=ProcessSupervisor(antigravity_bin=str(native_binary),runtime_home=home)
    supervisor.run_uid=1234
    monkeypatch.setattr(process_supervisor,'engine_processes',lambda:candidates)
    monkeypatch.setattr(supervisor,'_start_monitor',lambda:None)
    monkeypatch.setattr(supervisor,'detect_port_from_log',lambda:None)
    assert supervisor.find_existing() is native
    supervisor.refresh_runtime()
    assert supervisor.process.pid==99999 and supervisor.active_port is None
    assert supervisor.current_proxy_url=='http://127.0.0.1:12345'


def test_runtime_status_tracks_manual_open_close_and_keeps_saved_profile(tmp_path):
    async def scenario():
        app=create_app(config(tmp_path),supervisor=FakeSupervisor());state=app['state']
        profile=state.profiles.create_profile('saved');state.active=profile['id']
        state.cfg['engine_stopped']=True
        original=copy.deepcopy(profile)
        await state.refresh_runtime_status()
        assert not state.status()['engine_running'] and state.cfg['engine_stopped']
        state.supervisor.process=FakeProcess();state.supervisor.active_port=None
        await state.refresh_runtime_status()
        assert state.status()['engine_running'] and not state.status()['antigravity_ready']
        assert not state.cfg['engine_stopped']
        state.supervisor.active_port=45000
        await state.refresh_runtime_status()
        assert state.status()['antigravity_ready']
        state.supervisor.process=None
        # A stale port must not make the UI appear ready after the process exits.
        await state.refresh_runtime_status()
        assert not state.status()['engine_running'] and not state.status()['antigravity_ready']
        assert json.loads((tmp_path/'config.json').read_text())['engine_stopped']
        assert state.active==profile['id'] and state.profiles.get_profile(profile['id'])==original
        assert state.supervisor.events==[]
    asyncio.run(scenario())


def test_runtime_check_does_not_mark_intentional_restart_as_manual_close(tmp_path):
    async def scenario():
        app=create_app(config(tmp_path),supervisor=FakeSupervisor());state=app['state']
        state.supervisor.process=FakeProcess();await state.refresh_runtime_status()
        state.busy=True;state.supervisor.process=None
        await state.refresh_runtime_status()
        assert not state.cfg.get('engine_stopped')
        state.busy=False;state.supervisor.process=FakeProcess()
        await state.refresh_runtime_status()
        assert not state.cfg.get('engine_stopped')
    asyncio.run(scenario())


def test_stopped_root_redirects_to_panel_and_never_starts_default_profile(tmp_path):
    async def scenario():
        cfg=config(tmp_path);app=create_app(cfg,supervisor=FakeSupervisor());state=app['state']
        profile=state.profiles.create_profile('default');state.cfg['default_profile_id']=profile['id'];state.cfg['engine_stopped']=True
        runner,port=await start(app);url='http://127.0.0.1:'+str(port)
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                await login(client,url)
                response=await client.get(url+'/',allow_redirects=False)
                assert response.status==302 and response.headers['Location']=='/?portal=1'
                response=await client.get(url+'/?portal=1')
                assert response.status==200 and 'btn-engine-toggle' in await response.text()
                assert state.supervisor.events==[]
        finally:await runner.cleanup()
    asyncio.run(scenario())


@pytest.mark.skipif(os.name=='nt',reason='Linux native-user process environment')
def test_native_proxy_environment_helper_returns_only_requested_fields(tmp_path):
    from discovery import process_info
    from process_tracking import process_environment
    python=Path(getattr(__import__('sys'),'_base_executable',__import__('sys').executable)).resolve()
    child=subprocess.Popen([str(python),'-B','-c','import time;time.sleep(10)'],
                           env=dict(os.environ,HTTPS_PROXY='http://127.0.0.1:45678',OTHER_SECRET='do-not-return'))
    try:
        info=process_info(child.pid)
        result=process_environment(info,('HTTPS_PROXY',))
        assert result=={'HTTPS_PROXY':'http://127.0.0.1:45678'}
    finally:child.terminate();child.wait(timeout=3)


def test_native_last_window_close_quits_without_auth_logout(tmp_path):
    import shutil
    if not shutil.which('node'):pytest.skip('Requires Node.js for native event fixture')
    path=Path(__file__).parents[1]/'static/native-main.js'
    script=r'''
const vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync(process.argv[1],'utf8').replace('__AGY_BOOTSTRAP__','""').replace('__AGY_DOCK__','""');
for(const headless of [false,true]){
 let quit=0;const events={};
 const app={on:(name,fn)=>events[name]=fn,whenReady:()=>({then:()=>{}}),quit:()=>quit++,isReady:()=>false};
 const electron={app,session:{},protocol:{registerSchemesAsPrivileged:()=>{}},ipcMain:{handle:()=>{}},shell:{},webContents:{}};
 const context={module:{exports:{}},process:{env:{ELECTRON_OZONE_PLATFORM_HINT:headless?'headless':''}},Map,Set,WeakSet,URL,require:name=>name==='electron'?electron:name==='./suite-client-transform.js'?{transform:()=>{},blockedLogout:()=>false}:require(name)};
 vm.runInNewContext(source,context);context.module.exports.install();events['window-all-closed']();
 if(quit!==(headless?0:1))throw Error('Last desktop window must quit without closing headless runtime');
}
'''
    subprocess.run(['node','-e',script,str(path)],check=True,capture_output=True,text=True)
