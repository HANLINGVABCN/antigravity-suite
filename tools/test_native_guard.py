"""Opt-in Linux Electron integration using a cloned installation and mock login service.

Run: python tools/test_native_guard.py /opt/antigravity/antigravity /private/new-test-directory
Never reads a real token or calls a real Google logout. Requires an X display and openssl.
"""
import asyncio
import errno
import hashlib
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from desktop_guard import install,read_archive,encode_archive,MARKER

FIXTURE='''globalThis.AuthFixture=class {
async loginWithRedirect(a){let g=await this._lsClient.login({});var e=g.authResult;if(e?.hasValidAuth)this.result=e;}
async logout(){this.isLoggingOut=true;try{await this._lsClient.authLogout({});}finally{this.result=null;}}
};globalThis.testAuth=new AuthFixture();testAuth.isLoggingOut=false;testAuth.result='kept';
testAuth._lsClient={authLogout:async()=>fetch('/exa.language_server_pb.LanguageServerService/AuthLogout',{method:'POST'}),login:async()=>({authResult:{hasValidAuth:true}})};'''


async def run_native_fixture(binary,root,real_client=False):
    import aiohttp
    root.mkdir(mode=0o700)
    original=binary.parent/'resources/app.asar';digest=hashlib.sha256(original.read_bytes()).hexdigest()
    def clone_file(source,destination):
        try:os.link(source,destination)
        except OSError as error:
            if error.errno!=errno.EXDEV:raise
            shutil.copy2(source,destination)
    clone=root/'client';shutil.copytree(binary.parent,clone,symlinks=True,copy_function=clone_file)
    original_header,original_files=read_archive(original)
    if MARKER.encode() in original_files.get('dist/main.js',b''):
        candidates=[Path('/config/antigravity-suite-improved/data'),Path('/var/lib/antigravity-suite-improved')]
        if os.environ.get('ANTIGRAVITY_SUITE_DATA'):candidates.insert(0,Path(os.environ['ANTIGRAVITY_SUITE_DATA']))
        for folder in candidates:
            manifest=folder/'desktop-guard-backups/manifest.json'
            if not manifest.is_file():continue
            value=json.loads(manifest.read_text())
            if Path(value['archive']).resolve()!=original.resolve() or value['patched_sha256']!=digest:continue
            saved=Path(value['backup']);content=saved.read_bytes()
            assert hashlib.sha256(content).hexdigest()==value['original_sha256']
            dest=clone/'resources/app.asar';temporary=dest.with_name('fixture-original.asar')
            temporary.write_bytes(content);temporary.chmod(dest.stat().st_mode&0o777);os.replace(temporary,dest)
            break
        else:raise RuntimeError('Patched source requires its matching verified original-resource backup')
    # Keep the disposable client out of the live service's native-instance discovery.
    # Otherwise that scanner can overwrite the fixture's loopback capability file.
    clone_binary=clone/'antigravity-suite-guard-fixture';(clone/binary.name).rename(clone_binary)
    home=root/'home';home.mkdir();config=home/'.config/Antigravity';config.mkdir(parents=True)
    assert install(clone_binary,root/'data')['installed']
    archive=clone/'resources/app.asar';header,files=read_archive(archive)
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=localhost',
                    '-keyout',str(root/'key.pem'),'-out',str(root/'cert.pem')],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    requests=[];operations=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            requests.append(self.path)
            data=FIXTURE.encode() if self.path=='/main.js' else b'<html><head><script src="/main.js"></script></head><body>Isolated native fixture<div>Email dummy@example.invalid<button id="fixture-signout" onclick="globalThis.fixtureHandlerRuns++;testAuth.logout()">Sign Out</button></div></body></html>'
            self.send_response(200);self.send_header('Content-Type','application/javascript' if self.path=='/main.js' else 'text/html');self.end_headers();self.wfile.write(data)
        def do_POST(self):
            requests.append(self.path)
            payload=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))) or b'{}')
            operation=payload.get('operation');operations.append(operation)
            result=dict(ok=True,profiles=[],active_profile=None,active_proxy=None,intent_id='1'*32)
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(result).encode())
    backend=ThreadingHTTPServer(('127.0.0.1',0),Handler);control=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(root/'cert.pem',root/'key.pem');backend.socket=context.wrap_socket(backend.socket,server_side=True)
    for server in (backend,control):threading.Thread(target=server.serve_forever,daemon=True).start()
    cap=home/'.cache/antigravity-suite';cap.mkdir(parents=True)
    (cap/'native-control.json').write_text(json.dumps(dict(runtime_id='1'*32,token='2'*64,url='http://127.0.0.1:'+str(control.server_port))))
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));debug_port=sock.getsockname()[1]
    # The mock main runs the same original createWindow/preload in the real Electron binary.
    if not real_client:
        files['dist/main.js']=("require('./suite-native-main.js').install();const {app}=require('electron');app.whenReady().then(()=>require('./utils.js').createWindow('https://127.0.0.1:"+str(backend.server_port)+"/'));app.on('window-all-closed',()=>app.quit());").encode()
        replacement=archive.with_name('fixture.asar');replacement.write_bytes(encode_archive(header,files));os.replace(replacement,archive)
    log=(root/'electron.log').open('wb');env=dict(os.environ,HOME=str(home),XDG_CONFIG_HOME=str(home/'.config'))
    process=subprocess.Popen([str(clone_binary),'--no-sandbox','--disable-gpu','--ignore-certificate-errors','--remote-debugging-address=127.0.0.1','--remote-debugging-port='+str(debug_port)],env=env,stdout=log,stderr=log,start_new_session=True)
    try:
        async with aiohttp.ClientSession() as client:
            target=None
            for _ in range(100):
                if process.poll() is not None:raise RuntimeError('Isolated Electron exited; inspect private electron.log')
                try:
                    tabs=await(await client.get('http://127.0.0.1:'+str(debug_port)+'/json/list')).json()
                    target=next((tab for tab in tabs if tab['type']=='page' and tab['url'].startswith('https://127.0.0.1:')),None)
                    if target:break
                except (aiohttp.ClientError,ValueError):pass
                await asyncio.sleep(.2)
            if not target:raise RuntimeError('Native test renderer did not open')
            async with client.ws_connect(target['webSocketDebuggerUrl']) as ws:
                counter=0
                async def evaluate(expression):
                    nonlocal counter
                    counter+=1;identifier=counter
                    await ws.send_json(dict(id=identifier,method='Runtime.evaluate',params=dict(expression=expression,awaitPromise=True,returnByValue=True)))
                    while True:
                        message=await asyncio.wait_for(ws.receive_json(),10)
                        if message.get('id')==identifier:
                            if message.get('error'):
                                if 'context' in message['error'].get('message','').lower():return None
                                raise RuntimeError(str(message['error']))
                            if message.get('result',{}).get('exceptionDetails'):raise RuntimeError('Native test JS failed: '+str(message['result']['exceptionDetails']))
                            return message['result']['result'].get('value')
                for _ in range(100):
                    expression='!!globalThis.__AGY_SESSION__ && !!globalThis.antigravitySuiteNative && !!document.getElementById("ag-floating-btn")'
                    if not real_client:expression+=' && !!globalThis.testAuth'
                    if await evaluate(expression):break
                    await asyncio.sleep(.2)
                else:raise RuntimeError('Native sandbox preload or guarded renderer did not load')
                if real_client:
                    from auth_probe import auth_status
                    from types import SimpleNamespace
                    from urllib.parse import urlsplit
                    state=SimpleNamespace(cfg=dict(backend_scheme='https',backend_verify_tls=False),supervisor=SimpleNamespace(active_port=int(urlsplit(target['url']).port)),http=client,active=None,busy=False)
                    auth=await auth_status(state,force=True)
                    assert auth['token_present'] is False and 'ready' in operations
                    print(json.dumps(dict(real_client_startup=True,sandbox_preload=True,verified_native_bundle=True,empty_login_confirmed=True,desktop_dock=True,original_archive_unchanged=True)))
                    return
                result=await evaluate('''(async()=>{await testAuth.logout();return {kept:testAuth.result==='kept',loggingOut:testAuth.isLoggingOut};})()''')
                assert result==dict(kept=True,loggingOut=False)
                assert await evaluate('!!document.getElementById("agy-logout-warning")?.open')
                await evaluate('document.querySelector("#agy-logout-warning button").click()')
                assert await evaluate('!document.getElementById("ag-drawer")?.classList.contains("active") && testAuth.result==="kept" && !testAuth.isLoggingOut')
                await evaluate('globalThis.fixtureHandlerRuns=0;document.getElementById("fixture-signout").click()')
                for _ in range(20):
                    if await evaluate('!!document.getElementById("agy-logout-warning")?.open'):break
                    await asyncio.sleep(.1)
                else:raise RuntimeError('Native account click was not intercepted')
                assert await evaluate('fixtureHandlerRuns===0 && testAuth.result==="kept" && !testAuth.isLoggingOut')
                await evaluate('document.querySelector("#agy-logout-warning button").click()')
                await evaluate('testAuth.loginWithRedirect()');assert 'begin' in operations and 'complete' in operations
                # Bypass fetch guard using XHR to verify Electron's request guard too.
                xhr=await evaluate('''new Promise(resolve=>{const x=new XMLHttpRequest();x.open('POST','/exa.language_server_pb.LanguageServerService/AuthLogout');x.onload=()=>resolve('reached');x.onerror=()=>resolve('blocked');x.send('{}')})''')
                assert xhr=='blocked' and not any('AuthLogout' in path for path in requests)
                for _ in range(20):
                    if await evaluate('!!document.getElementById("agy-logout-warning")?.open'):break
                    await asyncio.sleep(.1)
                else:raise RuntimeError('Blocked native logout did not display its warning')
                assert 'ready' in operations
                print(json.dumps(dict(sandbox_preload=True,renderer_transform=True,logout_before_auth_mutation=True,warning_dismissal_preserves_login=True,account_click_intercepted=True,native_rpc_blocked=True,login_hooks=True,original_archive_unchanged=True)))
    finally:
        process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait()
        log.close()
        for server in (backend,control):server.shutdown();server.server_close()
        assert hashlib.sha256(original.read_bytes()).hexdigest()==digest


if __name__=='__main__':asyncio.run(run_native_fixture(Path(sys.argv[1]).resolve(),Path(sys.argv[2]).resolve(),'--real-client' in sys.argv[3:]))
