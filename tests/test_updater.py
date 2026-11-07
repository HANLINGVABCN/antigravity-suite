import asyncio
import copy
import hashlib
import json
import os
import shutil
import time
import zipfile
from pathlib import Path

import aiohttp
import pytest

from config import atomic_json
from server import create_app
from suite_updater import REPOSITORY, SuiteUpdater, extract_release, release_asset
from test_suite import FakeSupervisor, config, login, start
from update_worker import build_environment, run_update


def bundle(path,version='0.8.0',extras=None):
    files=dict(VERSION=version,**{'server.py':'# new version\n','config.py':'# config\n','requirements.txt':'',
        'static/portal.html':'<html>new</html>','update_worker.py':'# worker\n','service_control.py':'# service\n'})
    files.update(extras or {})
    with zipfile.ZipFile(path,'w') as archive:
        for name,text in files.items():archive.writestr('antigravity-suite/'+name,text)
    return files


def release(archive,version='0.8.0'):
    tag='v'+version;name='antigravity-suite-'+tag+'-source.zip'
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum=digest+'  '+name+'\n'
    return dict(tag_name=tag,html_url='https://github.com/'+REPOSITORY+'/releases/tag/'+tag,draft=False,prerelease=False,body='中文更新说明',
        assets=[dict(name=name,size=archive.stat().st_size,state='uploaded',digest='sha256:'+digest,
            browser_download_url='https://github.com/'+REPOSITORY+'/releases/download/'+tag+'/'+name),
            dict(name=name+'.sha256',size=len(checksum),state='uploaded',
            browser_download_url='https://github.com/'+REPOSITORY+'/releases/download/'+tag+'/'+name+'.sha256')]),checksum


@pytest.mark.parametrize('name',['../outside.py','data/config.json','config.json','profiles/account.py','.venv/bin/python','static/evil.exe','unknown/script.py','static/../server.py'])
def test_archive_rejects_private_data_traversal_and_unexpected_executables(tmp_path,name):
    archive=tmp_path/'source.zip';bundle(archive,extras={name:'bad'})
    with pytest.raises(ValueError):extract_release(archive,tmp_path/'source','0.8.0')
    assert not (tmp_path/'outside.py').exists()


def test_verified_archive_requires_matching_version_and_returns_file_checksums(tmp_path):
    archive=tmp_path/'source.zip';files=bundle(archive)
    hashes=extract_release(archive,tmp_path/'source','0.8.0')
    assert hashes['server.py']==hashlib.sha256(files['server.py'].encode()).hexdigest()
    with pytest.raises(ValueError,match='版本'):extract_release(archive,tmp_path/'wrong','0.9.0')
    meta,_=release(archive);name=meta['assets'][0]['name']
    meta['assets'][0]['browser_download_url']='https://evil.invalid/'+name
    with pytest.raises(ValueError,match='来源'):release_asset(meta,name)


def test_actual_public_source_layout_is_accepted_including_systemd_example(tmp_path):
    root=Path(__file__).parents[1];archive=tmp_path/'actual-public-source.zip'
    folders=['static','docs','examples','tests','tools','systemd','.github']
    files=[p for p in root.iterdir() if p.is_file() and p.suffix not in {'.zip','.gz'}]
    files+=[p for folder in folders for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    with zipfile.ZipFile(archive,'w') as package:
        for file in files:package.write(file,'antigravity-suite/'+file.relative_to(root).as_posix())
    extracted=extract_release(archive,tmp_path/'source',(root/'VERSION').read_text().strip())
    assert 'systemd/antigravity-suite-improved.service' in extracted
    assert 'update_worker.py' in extracted and 'static/update.js' in extracted


def update_fixture(tmp_path):
    live=tmp_path/'live';live.mkdir();(live/'VERSION').write_text('0.7.0');(live/'server.py').write_text('# original')
    environment=live/'.venv';environment.mkdir();(environment/'original').write_text('original environment')
    data=tmp_path/'private';data.mkdir();cfg=config(data)
    cfg.update(mfa_enabled=True,mfa_secret='dummy-authenticator-secret',mfa_recovery_hashes=['dummy-recovery-hash'])
    atomic_json(data/'config.json',cfg)
    atomic_json(data/'profiles.json',[dict(id='fixed-id',proxy_id='fixed-proxy',created_at='original')])
    atomic_json(data/'proxies.json',[dict(id='fixed-proxy',password='private-dummy')])
    chat=data/'shared-history/conversations';chat.mkdir(parents=True);(chat/'keep.txt').write_text('all chats')
    job=data/'updates/job';job.mkdir(parents=True);(data/'updates/update.lock').write_text('job')
    archive=tmp_path/'source.zip';bundle(archive);source=job/'source';files=extract_release(archive,source,'0.8.0')
    plan=dict(live=str(live),data=str(data),source=str(source),job_dir=str(job),job_id='job',state_file=str(data/'updates/status.json'),
              files=files,version='0.8.0',controller=dict(kind='fake'),started_at=time.time())
    def build(plan):
        path=job/'environment';path.mkdir();(path/'new').write_text('new environment');return path
    return plan,build


def test_update_worker_replaces_source_env_preserves_password_mfa_ids_nodes_and_chat_files(tmp_path):
    plan,build=update_fixture(tmp_path)
    data=Path(plan['data']);before={name:(data/name).read_bytes() for name in ('config.json','profiles.json','proxies.json')}
    calls=[]
    run_update(plan,service=lambda spec,action:calls.append(action),health=lambda plan,version:None,build=build)
    status=json.loads(Path(plan['state_file']).read_text())
    assert status['phase']=='success' and calls==['stop','start']
    assert (Path(plan['live'])/'VERSION').read_text()=='0.8.0'
    assert (Path(plan['live'])/'.venv/new').is_file()
    assert before=={name:(data/name).read_bytes() for name in before}
    assert (data/'shared-history/conversations/keep.txt').read_text()=='all chats'
    assert (Path(status['backup_dir'])/'private-data.tar.gz').is_file()
    assert not (data/'updates/update.lock').exists()


def test_failed_new_service_rolls_back_source_environment_and_preserves_data(tmp_path):
    plan,build=update_fixture(tmp_path);calls=[]
    def health(plan,version):
        if version=='0.8.0':raise RuntimeError('intentional new version failure with private-secret')
    run_update(plan,service=lambda spec,action:calls.append(action),health=health,build=build)
    status=json.loads(Path(plan['state_file']).read_text());live=Path(plan['live'])
    assert status['phase']=='error' and status['rollback_succeeded'] and calls==['stop','start','stop','start']
    assert (live/'VERSION').read_text()=='0.7.0' and (live/'server.py').read_text()=='# original'
    assert (live/'.venv/original').is_file() and not (live/'update_worker.py').exists()
    assert 'private-secret' not in json.dumps(status)
    assert (Path(plan['data'])/'shared-history/conversations/keep.txt').is_file()


def test_dependency_failure_keeps_service_and_source_running_without_stop(tmp_path):
    plan,_=update_fixture(tmp_path);calls=[]
    def fail(plan):raise RuntimeError('dependency failure')
    run_update(plan,service=lambda spec,action:calls.append(action),build=fail)
    status=json.loads(Path(plan['state_file']).read_text())
    assert status['phase']=='error' and calls==[]
    assert (Path(plan['live'])/'.venv/original').is_file()
    assert not (Path(plan['data'])/'updates/update.lock').exists()


def test_update_check_api_requires_login_and_update_blocks_other_mutations(tmp_path):
    async def scenario():
        app=create_app(config(tmp_path),supervisor=FakeSupervisor());state=app['state']
        archive=tmp_path/'release.zip';bundle(archive);meta,checksum=release(archive,'99.0.0')
        async def download(url,dest,limit,expected_size=None):
            if '/releases/latest' in url:Path(dest).write_text(json.dumps(meta))
        state.updater._download=download
        runner,port=await start(app);url='http://127.0.0.1:%d'%port
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                assert (await client.get(url+'/__suite/api/system/update/status')).status==401
                await login(client,url)
                response=await client.post(url+'/__suite/api/system/update/check')
                assert response.status==200 and (await response.json())['update_available']
                assert (await client.post(url+'/__suite/api/system/update/start',headers={'Origin':'https://evil.invalid'})).status==403
                atomic_json(state.updater.status_file,dict(phase='installing',job_id='dummy'))
                assert (await client.post(url+'/__suite/api/profiles',json=dict(name='cannot-create'))).status==409
                assert (await client.get(url+'/__suite/api/status')).status==200
        finally:await runner.cleanup()
    asyncio.run(scenario())


def test_checksum_failure_never_launches_worker_and_releases_update_lock(tmp_path,monkeypatch):
    async def scenario():
        app=create_app(config(tmp_path),supervisor=FakeSupervisor());updater=app['state'].updater
        archive=tmp_path/'release.zip';bundle(archive);meta,checksum=release(archive)
        job=tmp_path/'updates/job';job.mkdir(parents=True)
        updater.root.mkdir(exist_ok=True);(updater.root/'update.lock').write_text('job')
        plan=dict(job_id='job',job_dir=str(job),source=str(job/'source'),state_file=str(updater.status_file),version='0.8.0',started_at=time.time())
        async def download(url,dest,limit,expected_size=None):
            if url.endswith('.sha256'):Path(dest).write_text('0'*64+'  '+meta['assets'][0]['name'])
            else:shutil.copy2(archive,dest)
        updater._download=download
        import suite_updater
        monkeypatch.setattr(suite_updater,'launch_worker',lambda *args:pytest.fail('checksum failure must not launch worker'))
        await updater.prepare(plan,meta)
        assert updater.job_status()['phase']=='error' and not (updater.root/'update.lock').exists()
        assert not (job/'source').exists()
    asyncio.run(scenario())


def test_verified_release_preparation_and_repeated_click_submit_only_one_worker(tmp_path,monkeypatch):
    async def scenario():
        app=create_app(config(tmp_path),supervisor=FakeSupervisor());updater=app['state'].updater
        updater.controller=dict(kind='test')
        archive=tmp_path/'release.zip';bundle(archive,'99.0.0');meta,checksum=release(archive,'99.0.0')
        async def download(url,dest,limit,expected_size=None):
            if '/releases/latest' in url:Path(dest).write_text(json.dumps(meta))
            elif url.endswith('.sha256'):Path(dest).write_text(checksum)
            else:shutil.copy2(archive,dest)
        updater._download=download
        calls=[]
        import suite_updater
        monkeypatch.setattr(suite_updater,'launch_worker',lambda *args:calls.append(args))
        first=await updater.start()
        second=await updater.start()
        assert first['job']['job_id']==second['job']['job_id']
        await updater.task
        assert len(calls)==1
        plan=json.loads(Path(calls[0][3]).read_text())
        assert plan['version']=='99.0.0' and plan['files']['server.py']
        assert (Path(plan['job_dir'])/'update_worker.py').is_file()
        assert updater.job_status()['phase']=='verifying'
    asyncio.run(scenario())


def test_existing_virtualenv_can_be_copied_when_standard_venv_bootstrap_is_missing(tmp_path):
    import subprocess
    plan,_=update_fixture(tmp_path)
    original=Path(plan['live'])/'.venv';(original/'bin').mkdir();(original/'bin/python').write_text('dummy-python')
    calls=[]
    def runner(command,**kwargs):
        calls.append(command)
        if '-m' in command and 'venv' in command:raise subprocess.CalledProcessError(1,command)
    env=build_environment(plan,runner=runner)
    assert (env/'bin/python').read_text()=='dummy-python'
    assert (original/'original').read_text()=='original environment'
    assert any('pip' in command for command in calls)
