import asyncio
import gzip
import threading

import aiohttp
from aiohttp import web
import pytest

import client_assets
from client_assets import ClientAssets,accepts_gzip
from server import create_app
from test_suite import config,FakeSupervisor,start,login

SCRIPT=b'''globalThis.Fixture=class {
async loginWithRedirect(a){let g=await this._lsClient.login({});var e=g.authResult;if(e?.hasValidAuth)this.result=e;}
async logout(){this.isLoggingOut=true;try{await this._lsClient.authLogout({});}finally{this.result=null;}}
};'''+b'\n/* repeated fixture */'*5000


@pytest.mark.parametrize('header,expected',[
    ('gzip, deflate, br',True),('gzip;q=0, br',False),('*;q=1',True),
    ('gzip;q=0, *;q=1',False),('',False),('GZIP;q=0.5',True),('gzip;q=invalid',False)])
def test_encoding_negotiation_respects_gzip_quality(header,expected):
    assert accepts_gzip(header)==expected


def test_script_cache_changes_on_native_source_bootstrap_and_runtime_and_stays_bounded():
    async def scenario():
        cache=ClientAssets()
        first=await cache.guarded_script(SCRIPT,'',('A',1))
        assert first['supported'] and gzip.decompress(first['gzip'])==first['identity']
        assert len(first['gzip'])<len(first['identity'])//5
        assert await cache.guarded_script(SCRIPT,'',('A',1)) is first
        updated=await cache.guarded_script(SCRIPT+b'\n/* changed native */','',('A',1))
        assert updated is not first and updated['etag_identity']!=first['etag_identity']
        guarded=await cache.guarded_script(SCRIPT,'/* updated protection */',('A',1))
        assert guarded['etag_identity']!=first['etag_identity']
        another=await cache.guarded_script(SCRIPT,'',('B',2))
        assert another is not first and cache.entry is another
    asyncio.run(scenario())


def test_guard_transform_and_compression_do_not_block_api_loop(monkeypatch):
    async def scenario():
        started=threading.Event();release=threading.Event();real=client_assets.build_script
        def slow(*args):
            started.set();assert release.wait(5);return real(*args)
        monkeypatch.setattr(client_assets,'build_script',slow)
        cache=ClientAssets();task=asyncio.create_task(cache.guarded_script(SCRIPT,'',('A',1)))
        try:
            for _ in range(100):
                if started.is_set():break
                await asyncio.sleep(.01)
            assert started.is_set() and not task.done()
            # Reaching this await before releasing the worker proves the event loop stayed live.
            await asyncio.sleep(0)
        finally:release.set();await task
    asyncio.run(scenario())


def test_authenticated_gateway_gzip_revalidation_and_upstream_changes(tmp_path):
    async def scenario():
        source=[SCRIPT];seen=[]
        async def main(request):
            seen.append(dict(request.headers))
            response=web.Response(body=source[0],headers={'Content-Type':'application/javascript','ETag':'"vendor-script"','Cache-Control':'no-cache'})
            response.enable_compression();return response
        backend=web.Application();backend.router.add_get('/main.js',main);br,bp=await start(backend)
        app=create_app(config(tmp_path),supervisor=FakeSupervisor(bp));app['state'].active='accountA'
        sr,sp=await start(app);base='http://127.0.0.1:'+str(sp)
        try:
            async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True),auto_decompress=False) as client:
                await login(client,base)
                response=await client.get(base+'/main.js',headers={'Accept-Encoding':'gzip'})
                assert response.status==200 and response.headers['Content-Encoding']=='gzip'
                body=await response.read();decoded=gzip.decompress(body)
                assert b'__AGY_SESSION_GUARD_V2__' in decoded
                assert b'async logout(){await globalThis.__AGY_SESSION__.warn();}' in decoded
                assert len(body)<len(decoded)//5
                assert response.headers['Cache-Control']=='private, no-cache, must-revalidate'
                assert 'Accept-Encoding' in response.headers['Vary']
                etag=response.headers['ETag']
                again=await client.get(base+'/main.js',headers={'Accept-Encoding':'gzip','If-None-Match':etag,'If-Modified-Since':'Wed, 21 Oct 2015 07:28:00 GMT'})
                assert again.status==304 and await again.read()==b''
                assert 'If-None-Match' not in seen[-1] and 'If-Modified-Since' not in seen[-1]
                source[0]+=b'\n/* vendor changed */'
                changed=await client.get(base+'/main.js',headers={'Accept-Encoding':'gzip','If-None-Match':etag})
                assert changed.status==200 and changed.headers['ETag']!=etag
                plain=await client.get(base+'/main.js',headers={'Accept-Encoding':'gzip;q=0','If-None-Match':changed.headers['ETag']})
                assert plain.status==200 and 'Content-Encoding' not in plain.headers
                assert plain.headers['ETag']!=changed.headers['ETag']
                assert b'/* vendor changed */' in await plain.read()
            async with aiohttp.ClientSession() as stranger:
                denied=await stranger.get(base+'/main.js',headers={'If-None-Match':etag})
                assert denied.status==401
        finally:await sr.cleanup();await br.cleanup()
    asyncio.run(scenario())
