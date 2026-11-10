import asyncio
import io
import json
import tarfile

import aiohttp
import pytest

from config import atomic_json
from profile_manager import ProfileManager
from test_suite import config,FakeSupervisor,start,login
from server import create_app


def populate(manager):
    p=manager.create_profile('A');folder=manager.profile_dir(p['id'])
    atomic_json(folder/'store/jetski-standalone-oauth-token',dict(token=dict(refresh_token='dummy-token')))
    for base in (folder/'store/gemini_antigravity',folder/'home/.gemini/antigravity',manager.history.root):
        for relative in ('conversations/chat.pb','brain/session/note.md','conversation_summaries.db-wal'):
            file=base/relative;file.parent.mkdir(parents=True,exist_ok=True);file.write_text('private history fixture')
    (folder/'home/workspace').mkdir(parents=True);(folder/'home/workspace/source.py').write_text('workspace')
    return p


@pytest.mark.parametrize('history,proxy',[(False,False),(False,True),(True,False),(True,True)])
def test_export_choices_exclude_history_in_stores_and_homes_and_keep_proxy_independent(tmp_path,history,proxy):
    m=ProfileManager(tmp_path,tmp_path/'profiles');p=populate(m)
    node=dict(id='a'*8,name='node',protocol='http',host='example.invalid',port=8080,password='dummy-node-password') if proxy else None
    bundle=tmp_path/'out.tar.gz';m.export_profile(p['id'],bundle,node,history)
    with tarfile.open(bundle) as tar:
        names=tar.getnames();meta=json.load(tar.extractfile('profile/profile.json'))
        assert meta['export_options']==dict(include_history=history,include_proxy=proxy)
        assert ('profile/proxy.json' in names)==proxy
        assert 'profile/store/jetski-standalone-oauth-token' in names
        assert 'profile/home/workspace/source.py' not in names
        assert sum(name.endswith('/chat.pb') for name in names)==int(history)
        chats=[n for n in names if 'conversations/' in n or 'brain/' in n or 'conversation_summaries.db' in n]
        assert bool(chats)==history
        assert ('profile/shared-history/conversations/chat.pb' in names)==history
    assert (m.history.root/'conversations/chat.pb').is_file()


def test_export_api_honors_history_choice_and_omitted_history_choice_excludes_history(tmp_path):
    async def scenario():
        app=create_app(config(tmp_path),supervisor=FakeSupervisor());m=app['state'].profiles;p=populate(m)
        runner,port=await start(app);base='http://127.0.0.1:'+str(port)
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as client:
                await login(client,base)
                for option,expected in [('0',False),('1',True),(None,False)]:
                    params=dict(id=p['id'])
                    if option is not None:params['include_history']=option
                    response=await client.get(base+'/__suite/api/profiles/export',params=params)
                    assert response.status==200
                    with tarfile.open(fileobj=io.BytesIO(await response.read())) as tar:
                        assert ('profile/shared-history/conversations/chat.pb' in tar.getnames())==expected
                assert (await client.get(base+'/__suite/api/profiles/export',params=dict(id=p['id'],include_history='bad'))).status==400
        finally:await runner.cleanup()
    asyncio.run(scenario())
