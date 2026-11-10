import asyncio
import io
import json
import sqlite3
import tarfile
from pathlib import Path

import aiohttp
import pytest

from config import atomic_json
from profile_manager import ProfileManager
from shared_history import DATABASE
from server import create_app
from test_suite import config,FakeSupervisor,start,login


def history(root,label):
    (root/'conversations').mkdir(parents=True,exist_ok=True)
    (root/'brain'/label).mkdir(parents=True,exist_ok=True)
    (root/'conversations'/(label+'.pb')).write_bytes(label.encode())
    (root/'brain'/label/'note.md').write_text(label)
    with sqlite3.connect(root/DATABASE) as db:
        db.execute('CREATE TABLE IF NOT EXISTS conversation_summaries(conversation_id TEXT PRIMARY KEY,last_modified_time INTEGER)')
        db.execute('INSERT OR REPLACE INTO conversation_summaries VALUES (?,?)',(label,1))
    db.close()


def package(tmp_path,include_history=True):
    manager=ProfileManager(tmp_path/'source',tmp_path/'source/profiles')
    profile=manager.create_profile('Imported')
    atomic_json(manager.profile_dir(profile['id'])/'store/jetski-standalone-oauth-token',dict(token=dict(refresh_token='dummy-import')))
    history(manager.history.root,'incoming')
    file=tmp_path/'archive.tar.gz';manager.export_profile(profile['id'],file,include_history=include_history)
    return file


@pytest.mark.parametrize('include,mode,expected',[(False,'merge',{'old'}),(True,'merge',{'old','incoming'}),(True,'replace',{'incoming'})])
def test_import_history_is_explicit_and_merge_and_replace_have_distinct_results(tmp_path,include,mode,expected):
    archive=package(tmp_path)
    manager=ProfileManager(tmp_path/'target',tmp_path/'target/profiles')
    old=manager.create_profile('Existing',proxy_id='original-node')
    old_dir=manager.profile_dir(old['id']);inode=old_dir.stat().st_ino
    atomic_json(old_dir/'store/jetski-standalone-oauth-token',dict(token=dict(refresh_token='original-login')))
    history(manager.history.root,'old');(manager.history.root/'preferences.json').write_text('keep-software-setting')
    preview=manager.inspect_import(archive)
    assert preview['has_history'] and preview['history_files']==3
    imported=manager.import_profile(archive,include_history=include,history_mode=mode)
    assert imported['id']!=old['id'] and old_dir.stat().st_ino==inode
    assert not (manager.profile_dir(imported['id'])/'home').exists()
    assert json.loads((old_dir/'store/jetski-standalone-oauth-token').read_text())['token']['refresh_token']=='original-login'
    assert (manager.history.root/'preferences.json').read_text()=='keep-software-setting'
    assert {p.stem for p in (manager.history.root/'conversations').glob('*.pb')}==expected
    with sqlite3.connect(manager.history.root/DATABASE) as db:
        assert {r[0] for r in db.execute('SELECT conversation_id FROM conversation_summaries')}==expected


def test_no_history_package_does_not_offer_or_allow_chat_import(tmp_path):
    archive=package(tmp_path,False);manager=ProfileManager(tmp_path/'target',tmp_path/'target/profiles')
    assert manager.inspect_import(archive)['history_message']=='无聊天文件'
    with pytest.raises(ValueError,match='无聊天文件'):manager.import_profile(archive,include_history=True)
    assert not manager.list_profiles()


def test_failed_history_swap_rolls_back_all_old_files_and_new_account(tmp_path,monkeypatch):
    import history_transfer
    archive=package(tmp_path);manager=ProfileManager(tmp_path/'target',tmp_path/'target/profiles')
    old=manager.create_profile('Existing');history(manager.history.root,'old')
    original=history_transfer.os.replace
    def fail(source,dest):
        if Path(source).parts[-2:]==('shared-history','brain') and Path(dest)==manager.history.root/'brain':raise OSError('intentional swap failure')
        return original(source,dest)
    monkeypatch.setattr(history_transfer.os,'replace',fail)
    with pytest.raises(OSError):manager.import_profile(archive,include_history=True,history_mode='replace')
    assert manager.list_profiles()==[old]
    assert (manager.history.root/'conversations/old.pb').read_bytes()==b'old'
    assert (manager.history.root/'brain/old/note.md').read_text()=='old'
    assert not (manager.history.root/'conversations/incoming.pb').exists()
    assert not list(manager.history.root.parent.glob('.history-transfer-*'))


def test_incomplete_transfer_is_recovered_on_next_start(tmp_path):
    from history_transfer import recover
    archive=package(tmp_path);manager=ProfileManager(tmp_path/'target',tmp_path/'target/profiles')
    history(manager.history.root,'old')
    incoming=tmp_path/'incoming';history(incoming,'incoming')
    transfer=manager.history.prepare_import([incoming],'replace')
    transfer.apply();transfer.temporary._finalizer.detach()
    assert not (manager.history.root/'conversations/old.pb').exists()
    recover(manager.history)
    assert (manager.history.root/'conversations/old.pb').read_bytes()==b'old'
    assert not (manager.history.root/'conversations/incoming.pb').exists()
    assert not transfer.folder.exists()


def test_rollback_retry_keeps_files_that_were_already_restored(tmp_path,monkeypatch):
    import history_transfer
    manager=ProfileManager(tmp_path/'target',tmp_path/'target/profiles')
    history(manager.history.root,'old')
    incoming=tmp_path/'incoming';history(incoming,'incoming')
    transfer=manager.history.prepare_import([incoming],'replace');transfer.apply()
    original=history_transfer.os.replace;failed=False
    def fail_once(source,dest):
        nonlocal failed
        if Path(source)==transfer.old/'brain' and not failed:
            failed=True;raise OSError('temporary rollback failure')
        return original(source,dest)
    monkeypatch.setattr(history_transfer.os,'replace',fail_once)
    with pytest.raises(OSError,match='temporary rollback failure'):transfer.rollback()
    with sqlite3.connect(manager.history.root/DATABASE) as db:
        assert {r[0] for r in db.execute('SELECT conversation_id FROM conversation_summaries')}=={'old'}
    transfer.close()
    assert (manager.history.root/'conversations/old.pb').read_bytes()==b'old'
    assert (manager.history.root/'brain/old/note.md').read_text()=='old'
    with sqlite3.connect(manager.history.root/DATABASE) as db:
        assert {r[0] for r in db.execute('SELECT conversation_id FROM conversation_summaries')}=={'old'}
    assert not transfer.folder.exists()


def test_legacy_history_attachment_requires_an_explicit_import_choice(tmp_path):
    source=tmp_path/'legacy';(source/'store').mkdir(parents=True)
    atomic_json(source/'profile.json',dict(name='legacy'))
    history(source/'store/gemini_antigravity','old-format')
    archive=tmp_path/'legacy.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:tar.add(source,arcname='profile')
    manager=ProfileManager(tmp_path/'target',tmp_path/'target/profiles')
    assert manager.inspect_import(archive)['has_history']
    profile=manager.import_profile(archive)
    assert not (manager.history.root/'conversations/old-format.pb').exists()
    assert not (manager.profile_dir(profile['id'])/'store/gemini_antigravity').exists()
    manager.import_profile(archive,include_history=True)
    assert (manager.history.root/'conversations/old-format.pb').read_bytes()==b'old-format'


def test_api_replace_requires_confirmation_and_preserves_selected_login(tmp_path):
    async def scenario():
        archive=package(tmp_path)
        supervisor=FakeSupervisor();app=create_app(config(tmp_path/'target'),supervisor=supervisor);state=app['state']
        existing=state.profiles.create_profile('Existing');state.active=existing['id']
        history(state.profiles.history.root,'old')
        runner,port=await start(app);url='http://127.0.0.1:'+str(port)
        async def preview(client):
            form=aiohttp.FormData();form.add_field('file',archive.read_bytes(),filename='p.tar.gz',content_type='application/gzip')
            return await (await client.post(url+'/__suite/api/profiles/import/preview',data=form)).json()
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                await login(client,url);info=await preview(client)
                args=dict(import_token=info['import_token'],include_history=True,history_mode='replace')
                response=await client.post(url+'/__suite/api/profiles/import/confirm',json=args)
                assert response.status==400 and info['import_token'] in state.pending_imports
                assert (state.profiles.history.root/'conversations/old.pb').exists()
                response=await client.post(url+'/__suite/api/profiles/import/confirm',json=dict(args,confirm_replace=True))
                result=await response.json()
                assert response.status==200 and result['history_imported']
                assert state.active==existing['id'] and not (state.profiles.history.root/'conversations/old.pb').exists()
        finally:await runner.cleanup()
    asyncio.run(scenario())
