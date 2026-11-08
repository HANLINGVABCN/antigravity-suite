import asyncio
import copy
import json
import time
from pathlib import Path

import aiohttp
import pytest

from config import atomic_json
from restart_worker import run_restart
from server import create_app
from test_suite import config,FakeSupervisor,start,login


def test_restart_worker_recovers_old_port_on_health_failure_without_changing_credentials(tmp_path,monkeypatch):
    cfg=config(tmp_path);cfg['listen_port']=29999;atomic_json(tmp_path/'config.json',cfg)
    calls=[];checks=[];monkeypatch.setattr('restart_worker.time.sleep',lambda seconds:None)
    plan=dict(data=str(tmp_path),controller=dict(kind='fixture'),version='0.9.1',job_id='dummy',started_at=time.time(),state_file=str(tmp_path/'status.json'),old_port=18888,old_host='127.0.0.1')
    def health(*args):
        checks.append(True)
        if len(checks)==1:raise RuntimeError('fixture startup failed')
    run_restart(plan,service=lambda spec,action:calls.append(action),health=health)
    after=json.loads((tmp_path/'config.json').read_text());assert after==dict(cfg,listen_port=18888)
    job=json.loads((tmp_path/'status.json').read_text());assert job['phase']=='error' and job['restored']
    assert calls==['stop','start','stop','start']


def test_restart_api_authorization_idempotence_and_plan_does_not_share_password(tmp_path,monkeypatch):
    async def scenario():
        cfg=config(tmp_path);app=create_app(cfg,supervisor=FakeSupervisor());s=app['state'];s.updater.controller=dict(kind='fixture')
        launched=[];monkeypatch.setattr('service_restart.launch_worker',lambda *args:launched.append(args))
        runner,port=await start(app);url='http://127.0.0.1:'+str(port)
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                assert (await client.post(url+'/__suite/api/system/service-restart',json={})).status==401
                await login(client,url)
                first=await client.post(url+'/__suite/api/system/service-restart',json={});assert first.status==202
                data=await first.json();job=data['job']['job_id']
                again=await client.post(url+'/__suite/api/system/service-restart',json={});assert (await again.json())['job']['job_id']==job
                assert len(launched)==1
                plan=(tmp_path/'service-restarts'/job/'plan.json').read_text()
                assert cfg['session_secret'] not in plan and cfg['auth_password_hash'] not in plan
                assert (await client.post(url+'/__suite/api/profiles',json=dict(name='busy'))).status==409
                atomic_json(s.service_restart.file,dict(phase='success',job_id=job,message='fixture'))
        finally:await runner.cleanup()
    asyncio.run(scenario())


def test_restart_running_profile_resumes_it_without_changing_the_default(tmp_path,monkeypatch):
    cfg=config(tmp_path);atomic_json(tmp_path/'config.json',cfg);monkeypatch.setattr('restart_worker.time.sleep',lambda seconds:None)
    plan=dict(data=str(tmp_path),controller=dict(kind='fixture'),version='0.9.2',job_id='dummy',started_at=time.time(),state_file=str(tmp_path/'status.json'),old_port=18888,old_host='127.0.0.1',resume_profile_id='a'*32)
    calls=[]
    run_restart(plan,service=lambda spec,action:calls.append(action),health=lambda plan,version:None)
    after=json.loads((tmp_path/'config.json').read_text())
    assert after['restart_resume_profile_id']=='a'*32 and after['default_profile_id']==cfg['default_profile_id']
    assert json.loads((tmp_path/'status.json').read_text())['phase']=='success'
    assert calls==['stop','start']
