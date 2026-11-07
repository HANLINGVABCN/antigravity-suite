import asyncio
import base64
import hashlib
import hmac
import json
import logging
import secrets
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web
from config import DATA_DIR, SUITE_DIR, atomic_json, check_password, hash_password, load_config, save_config
from core_installer import CoreInstaller
from gateway import proxy
from process_supervisor import ProcessSupervisor
from profile_manager import ProfileManager
from proxy_manager import ProxyManager
from privacy import privacy_guard

class SuiteState:
    def __init__(self,cfg,profile_mgr=None,supervisor=None):
        self.cfg=cfg; self.static=SUITE_DIR/'static'; self.data=Path(cfg.get('data_dir',DATA_DIR))
        self.cfg['data_dir']=str(self.data)
        self.cfg.setdefault('core_directory',str(self.data/'core'))
        self.profiles=profile_mgr or ProfileManager(self.data,cfg['profiles_dir'],cfg['runtime_home'])
        self.proxies=ProxyManager(self.data)
        self.supervisor=supervisor or ProcessSupervisor(cfg['antigravity_bin'],cfg['display_num'],runtime_home=cfg['runtime_home'])
        self.installer=CoreInstaller(cfg,self.supervisor)
        self.active=cfg.get('active_profile_id'); self.loaded=None; self.busy=False; self.last_error=''
        self.lock=asyncio.Lock(); self.pending_oauth={}; self.login_attempts={}; self.http=None
        self.launch_task=None; self.starting_profile_id=None; self.diagnostics={}; self.detection_tasks={}
        self.outbound_bridge=None; self.engine_proxy_url=None
        self.last_switch=0
    async def prepare_engine_proxy(self, profile):
        from outbound_proxy import OutboundProxy
        if self.outbound_bridge: await self.outbound_bridge.close()
        self.outbound_bridge=await OutboundProxy(self.proxy_url(profile)).__aenter__()
        self.engine_proxy_url=self.outbound_bridge.url
        return self.engine_proxy_url
    async def ensure_switch_allowed(self):
        if self.launch_task and not self.launch_task.done():raise ValueError('正在启动档案，请等待完成')
    def bind_engine_home(self, p_id):
        self.profiles.bind_home(p_id)
        if hasattr(self.supervisor,'set_home'): self.supervisor.set_home(self.profiles.home)
    def persist(self):
        self.cfg['active_profile_id']=self.active
        atomic_json(self.data/'config.json',self.cfg)
    def proxy_url(self,p):
        if not p or not p.get('proxy_id'): return None
        item=self.proxies.get_proxy(p['proxy_id'])
        if not item: raise ValueError('绑定的代理不存在，请重新选择')
        return self.proxies.get_proxy_url(item)
    def request_start(self, target, restart=False):
        if not self.profiles.get_profile(target): raise ValueError('档案不存在')
        if target==self.active and self.supervisor.active_port and not self.busy and not restart: return
        if self.launch_task and not self.launch_task.done():
            if self.starting_profile_id==target: return
            raise ValueError('已有档案正在启动，请等待完成')
        if time.monotonic()-self.last_switch<4: raise ValueError('切换过于频繁，请等待几秒后重试')
        self.last_switch=time.monotonic()
        self.starting_profile_id=target
        async def launch():
            try:
                await self.activate(target)
                self.request_detection(target)
            except Exception as e:
                self.last_error=str(e)
                self.diagnostics[target]=dict(status='abnormal',severity='error',reason='可能是启动环境、运行文件或代理连接存在问题：'+str(e),checked_at=time.time())
            finally: self.starting_profile_id=None
        self.launch_task=asyncio.create_task(launch())
    def request_detection(self, target):
        if not self.profiles.get_profile(target): raise ValueError('档案不存在')
        task=self.detection_tasks.get(target)
        if task and not task.done(): return
        self.diagnostics[target]=dict(status='checking',reason='正在检查登录、代理和引擎连接',checked_at=None)
        async def detect():
            from diagnostics import run_detection
            try: await run_detection(self,target)
            except Exception:
                self.diagnostics[target]=dict(status='abnormal',severity='error',reason='可能是检测进程、网络连接或凭据读取异常，请查看服务日志',checked_at=time.time())
        self.detection_tasks[target]=asyncio.create_task(detect())
    async def activate(self,target):
        p=self.profiles.get_profile(target)
        if not p: raise ValueError('档案不存在')
        if self.installer.task and not self.installer.task.done(): raise ValueError('本体操作中，请等待完成')
        async with self.lock:
            old=self.active; old_profile=self.profiles.get_profile(old); old_loaded=self.loaded
            old_running=bool(self.supervisor.process and self.supervisor.process.poll() is None)
            proxy_url=self.proxy_url(p); self.busy=True; self.supervisor.is_restarting=True
            switched=False
            try:
                if p.get('proxy_id'):
                    result=await asyncio.to_thread(self.proxies.test_proxy,p['proxy_id'])
                    if not result.get('ok'): raise ValueError('可能是代理不可用或鉴权失败，已阻止启动，避免改走直连')
                await asyncio.to_thread(self.supervisor.stop_antigravity)
                if old_profile and old_loaded==old: await asyncio.to_thread(self.profiles.save_active_to_profile,old)
                await asyncio.to_thread(self.profiles.restore_profile,target)
                self.bind_engine_home(target)
                switched=True
                self.loaded=target
                proxy_url=await self.prepare_engine_proxy(p)
                await asyncio.to_thread(self.supervisor.start_antigravity,proxy_url)
                self.active=target; self.last_error=''; self.persist()
            except Exception as e:
                self.last_error=str(e)
                await asyncio.to_thread(self.supervisor.stop_antigravity)
                if old_profile:
                    try:
                        if switched:
                            await asyncio.to_thread(self.profiles.restore_profile,old)
                            self.bind_engine_home(old)
                        if old_running:
                            await self.prepare_engine_proxy(old_profile)
                            await asyncio.to_thread(self.supervisor.start_antigravity,self.engine_proxy_url)
                    except Exception: self.last_error+='；原档案恢复启动失败，请查看日志'
                self.active=old; self.loaded=old_loaded; self.persist()
                raise
            finally:
                self.busy=False; self.supervisor.is_restarting=False
    async def stop_and_save(self):
        self.busy=True; self.supervisor.is_restarting=True
        try:
            await asyncio.to_thread(self.supervisor.stop_antigravity)
            if self.loaded==self.active and self.profiles.get_profile(self.active):
                await asyncio.to_thread(self.profiles.save_active_to_profile,self.active)
        finally:
            self.busy=False; self.supervisor.is_restarting=False
    def status(self):
        p=self.profiles.get_profile(self.active)
        px=self.proxies.get_proxy(p.get('proxy_id')) if p and p.get('proxy_id') else None
        if px: px={k:v for k,v in px.items() if k!='password'}
        port=None if self.busy or self.starting_profile_id else self.supervisor.active_port
        return dict(active_profile=p,active_profile_id=self.active,active_proxy=px,
                    default_profile_id=self.cfg.get('default_profile_id'),antigravity_ready=bool(port),
                    antigravity_port=port,active_port=port,busy=self.busy,last_error=self.last_error,
                    starting_profile_id=self.starting_profile_id,diagnostics=self.diagnostics,
                    privacy=dict(enabled=True,browser_external_connections='blocked',client_ip_headers='removed',profile_homes='separate'),
                    history=self.profiles.history.summary(),
                    profiles=self.profiles.list_profiles(),proxies=[{k:v for k,v in p.items() if k!='password'} for p in self.proxies.list_proxies()],system=system_memory())

def system_memory():
    try:
        mem={line.split(':')[0]:int(line.split(':')[1].split()[0]) for line in Path('/proc/meminfo').read_text().splitlines()}
        return dict(mem_total='%dMB'%(mem['MemTotal']//1024),mem_used='%dMB'%((mem['MemTotal']-mem['MemAvailable'])//1024),swap_total='%dMB'%(mem['SwapTotal']//1024),swap_used='%dMB'%((mem['SwapTotal']-mem['SwapFree'])//1024))
    except (OSError,KeyError,ValueError): return {}

def session_token(cfg):
    text=cfg['auth_user']+'.'+str(int(time.time()))
    return text+'.'+hmac.new(cfg['session_secret'].encode(),text.encode(),hashlib.sha256).hexdigest()

def valid_session(token,cfg):
    try:
        user,stamp,sig=token.rsplit('.',2)
        age=time.time()-int(stamp)
        text=user+'.'+stamp
        expected=hmac.new(cfg['session_secret'].encode(),text.encode(),hashlib.sha256).hexdigest()
        return user==cfg['auth_user'] and 0<=age<30*86400 and hmac.compare_digest(sig,expected)
    except (ValueError,TypeError,AttributeError): return False

def secure_cookie(request,cfg):
    setting=cfg.get('cookie_secure','auto')
    if isinstance(setting,bool): return setting
    if request.secure: return True
    if cfg.get('trusted_proxy') and request.remote in ('127.0.0.1','::1'):
        return request.headers.get('X-Forwarded-Proto','').split(',')[0].strip()=='https'
    return False

@web.middleware
async def guard(request,handler):
    s=request.app['state']
    public=request.path in ('/__suite/api/login','/__suite/api/auth/google/callback')
    authenticated=valid_session(request.cookies.get('ag_session'),s.cfg)
    if not public and not authenticated:
        if request.path=='/' and request.method=='GET': return web.FileResponse(s.static/'login.html',headers={'Cache-Control':'no-store'})
        return web.json_response({'ok':False,'error':'未登录'},status=401)
    if request.path.startswith('/__suite/api/') and request.method not in ('GET','HEAD','OPTIONS'):
        origin=request.headers.get('Origin')
        if origin and urlsplit(origin).netloc!=request.host: return web.json_response({'ok':False,'error':'请求来源不匹配'},status=403)
    try: response=await handler(request)
    except (ValueError,KeyError,TypeError,json.JSONDecodeError) as e: return web.json_response({'ok':False,'error':str(e)},status=400)
    except web.HTTPException: raise
    except Exception:
        logging.exception('请求执行失败')
        return web.json_response({'ok':False,'error':'操作失败，请查看服务日志'},status=500)
    if request.path.startswith('/__suite/'):
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
    return response

async def api(request):
    s=request.app['state']; path=request.match_info['tail']; method=request.method
    d={}
    if method=='POST' and path!='profiles/import':
        if request.can_read_body:
            if request.content_type!='application/json': raise ValueError('请求需要使用 JSON 格式')
            d=await request.json()
            if not isinstance(d,dict): raise ValueError('请求必须是 JSON 对象')
    def ok(**values): return web.json_response(dict(ok=True,**values))
    def require_proxy(px):
        if px and not s.proxies.get_proxy(px): raise ValueError('代理不存在')
    if path=='login' and method=='POST':
        ip=request.remote or 'unknown'; now=time.monotonic()
        attempts=[t for t in s.login_attempts.get(ip,[]) if now-t<60]
        s.login_attempts[ip]=attempts
        if len(attempts)>=10: return web.json_response({'ok':False,'error':'尝试次数过多，请稍后重试'},status=429)
        user=d.get('username','').strip(); password=d.get('password','')
        valid=await asyncio.to_thread(check_password,password,s.cfg['auth_password_hash'])
        if user!=s.cfg['auth_user'] or not valid:
            attempts.append(now)
            return web.json_response({'ok':False,'error':'用户名或密码错误'},status=401)
        s.login_attempts.pop(ip,None)
        response=ok()
        response.set_cookie('ag_session',session_token(s.cfg),httponly=True,samesite='Lax',secure=secure_cookie(request,s.cfg),max_age=30*86400,path='/')
        return response
    if path=='logout' and method=='POST':
        response=ok(); response.del_cookie('ag_session',path='/'); return response
    if path=='status' and method=='GET': return web.json_response(s.status())
    if path=='profiles':
        if method=='GET': return web.json_response(s.profiles.list_profiles())
        if method=='POST':
            require_proxy(d.get('proxy_id'))
            async with s.lock: p=await asyncio.to_thread(s.profiles.create_profile,d.get('name',''),d.get('proxy_id',''))
            return web.json_response(p)
        if method=='DELETE':
            p_id=request.query.get('id'); s.profiles.profile_dir(p_id)
            async with s.lock:
                if s.active==p_id: await s.stop_and_save(); s.active=None
                await asyncio.to_thread(s.profiles.delete_profile,p_id)
                if s.cfg.get('default_profile_id')==p_id: s.cfg['default_profile_id']=None
                s.persist()
            return ok()
    if path=='profiles/switch' and method=='POST':
        s.request_start(d.get('profile_id'))
        return web.json_response(dict(ok=True,status='starting',profile_id=d.get('profile_id')),status=202)
    if path=='profiles/detect' and method=='POST':
        s.request_detection(d.get('profile_id'))
        return web.json_response(dict(ok=True,status='checking'),status=202)
    if path=='profiles/set-default' and method=='POST':
        p=d.get('profile_id')
        if p: s.profiles.profile_dir(p)
        async with s.lock: s.cfg['default_profile_id']=p; s.persist()
        return ok(default_profile_id=p)
    if path=='profiles/detail' and method=='GET':
        p_id=request.query.get('id'); folder=s.profiles.profile_dir(p_id)
        files=[dict(path=str(p.relative_to(folder/'store')).replace('\\','/'),size=p.stat().st_size) for p in (folder/'store').rglob('*') if p.is_file() and not p.is_symlink()]
        return ok(profile=s.profiles.get_profile(p_id),files=files)
    if path=='profiles/update' and method=='POST':
        if set(d)-{'id','name','proxy_id','email'}:raise ValueError('档案 ID 和创建时间不可修改')
        require_proxy(d.get('proxy_id'))
        async with s.lock:
            p=s.profiles.get_profile(d.get('id'))
            if not p: raise ValueError('档案不存在')
            if s.active==p['id'] and s.supervisor.active_port and d.get('proxy_id',p['proxy_id'])!=p['proxy_id']:
                raise ValueError('修改当前档案代理前，请先停止 IDE')
            p=await asyncio.to_thread(s.profiles.update_profile,p['id'],d.get('name'),d.get('proxy_id'),d.get('email'))
        return ok(profile=p)
    if path=='profiles/file' and method in ('GET','POST'):
        p_id=request.query.get('id') if method=='GET' else d.get('id')
        rel=request.query.get('path','') if method=='GET' else d.get('path','')
        async with s.lock:
            if p_id==s.active and s.supervisor.process and s.supervisor.process.poll() is None:
                raise ValueError('请先停止 IDE，再读取或修改当前档案文件')
            file=s.profiles.resolve_file(p_id,rel)
            runtime_file=s.profiles.file_in_home(p_id,rel)
            if method=='GET':
                if not file.is_file() or file.stat().st_size>1024*1024: raise ValueError('只能编辑 1MB 以内的文本文件')
                return ok(content=file.read_text(encoding='utf-8'))
            content=d.get('content','')
            if not isinstance(content,str) or len(content.encode())>1024*1024: raise ValueError('文件不能超过 1MB')
            if file.suffix=='.json' or file.name=='jetski-standalone-oauth-token': json.loads(content)
            file.parent.mkdir(parents=True,exist_ok=True)
            if file.exists():
                backup=s.data/'file-backups'/('%d-%s'%(time.time_ns(),p_id)); backup.mkdir(parents=True,mode=0o700)
                (backup/file.name).write_bytes(file.read_bytes())
            fd,name=tempfile.mkstemp(dir=file.parent,prefix='.edit-')
            import os
            try:
                with os.fdopen(fd,'w',encoding='utf-8') as out: out.write(content); out.flush(); os.fsync(out.fileno())
                os.replace(name,file); os.chmod(file,0o600)
            finally:
                if os.path.exists(name): os.unlink(name)
            # Persistent HOME is authoritative once initialized. Apply the edit there too.
            home=s.profiles.home_for(p_id)
            if runtime_file is not None and (home/'.suite-home-initialized').exists():
                runtime_file.parent.mkdir(parents=True,exist_ok=True)
                runtime_file.write_text(content,encoding='utf-8');os.chmod(runtime_file,0o600)
        return ok()
    if path=='profiles/import' and method=='POST':
        reader=await request.multipart(); field=await reader.next()
        if not field or field.name!='file': raise ValueError('请上传 file 字段')
        with tempfile.TemporaryDirectory() as tmp:
            file=Path(tmp)/'profile.tar.gz'; size=0
            with file.open('wb') as out:
                while True:
                    chunk=await field.read_chunk()
                    if not chunk: break
                    size+=len(chunk)
                    if size>64*1024*1024: raise ValueError('上传包不能超过 64MB')
                    out.write(chunk)
            async with s.lock: p=await asyncio.to_thread(s.profiles.import_profile,file)
        return ok(profile=p)
    if path=='profiles/export' and method=='GET':
        p_id=request.query.get('id'); s.profiles.profile_dir(p_id)
        async with s.lock:
            restart=p_id==s.active and bool(s.supervisor.active_port)
            if p_id==s.active: await s.stop_and_save()
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    file=Path(tmp)/'profile.tar.gz'
                    await asyncio.to_thread(s.profiles.export_profile,p_id,file)
                    data=await asyncio.to_thread(file.read_bytes)
            finally:
                if restart: await asyncio.to_thread(s.supervisor.start_antigravity,s.engine_proxy_url)
        return web.Response(body=data,content_type='application/gzip',headers={'Content-Disposition':'attachment; filename="profile_%s.tar.gz"'%p_id})
    if path=='proxies':
        if method=='GET': return web.json_response([{k:v for k,v in p.items() if k!='password'} for p in s.proxies.list_proxies()])
        if method=='POST':
            async with s.lock: p=await asyncio.to_thread(s.proxies.add_from_input,d)
            return ok(proxy={k:v for k,v in p.items() if k!='password'})
        if method=='DELETE':
            px=request.query.get('id')
            async with s.lock:
                if any(p.get('proxy_id')==px for p in s.profiles.list_profiles()): raise ValueError('代理仍绑定在档案中，请先解除绑定')
                s.proxies.delete_proxy(px)
            return ok()
    if path=='proxies/detail' and method=='GET':
        async with s.lock:
            item=s.proxies.get_proxy(request.query.get('id'))
            if not item:raise ValueError('代理节点不存在')
            proxy=s.proxies.public_proxy(item,reveal_password=request.query.get('reveal_password')=='1')
            if request.query.get('include_link')=='1':proxy['link']=s.proxies.get_proxy_link(item)
            bound=[dict(id=p['id'],name=p['name']) for p in s.profiles.list_profiles() if p.get('proxy_id')==item['id']]
        return ok(proxy=proxy,bound_profiles=bound)
    if path=='proxies/update' and method=='POST':
        proxy_id=d.get('id')
        fields={key:value for key,value in d.items() if key!='id'}
        async with s.lock:
            await s.ensure_switch_allowed()
            candidate=s.proxies.prepare_update(proxy_id,fields)
            item=s.proxies.get_proxy(proxy_id)
            connection_changed=any(item.get(key)!=candidate.get(key) for key in ('protocol','host','port','username','password'))
            active=s.profiles.get_profile(s.active)
            running=bool(s.supervisor.process and s.supervisor.process.poll() is None)
            if connection_changed and running and active and active.get('proxy_id')==proxy_id:
                return web.json_response(dict(ok=False,error='此节点正被当前 IDE 使用。请先停止 IDE，再修改连接信息；节点 ID 和绑定保持原值。',requires_stop=True),status=409)
            updated=await asyncio.to_thread(s.proxies.update_proxy,proxy_id,fields)
            if connection_changed:
                for p in s.profiles.list_profiles():
                    if p.get('proxy_id')==proxy_id:s.diagnostics.pop(p['id'],None)
                s._auth_cache=None
        return ok(proxy=s.proxies.public_proxy(updated),connection_changed=connection_changed)
    if path=='proxies/test' and method=='POST': return web.json_response(await asyncio.to_thread(s.proxies.test_proxy,d.get('proxy_id')))
    if path=='proxies/import-links' and method=='POST':
        async with s.lock: nodes=await asyncio.to_thread(s.proxies.import_links,d.get('links',''))
        return ok(proxies=[{k:v for k,v in node.items() if k!='password'} for node in nodes])
    if path=='system/core-status' and method=='GET':
        proc=s.supervisor.process
        file=Path(s.cfg['antigravity_bin'])
        return ok(installed=s.supervisor.installed(),size_mb=round(file.stat().st_size/1024/1024,1) if file.exists() else 0,running=bool(proc and proc.poll() is None),active_port=s.supervisor.active_port,install_state=s.installer.state,managed=s.installer.managed_directory() in file.resolve().parents)
    if path=='system/auth-status' and method=='GET':
        from auth_probe import auth_status
        return ok(**(await auth_status(s,force=request.query.get('force')=='1')))
    if path=='system/install-core' and method=='POST':
        async with s.lock:
            await s.ensure_switch_allowed()
            s.installer.start()
        return ok(state=s.installer.state,message='安装任务已提交')
    if path=='system/uninstall-core' and method=='POST':
        async with s.lock:
            if s.launch_task and not s.launch_task.done():raise ValueError('正在启动档案，请等待完成')
            if s.installer.task and not s.installer.task.done():raise ValueError('已有本体操作正在进行')
            await s.stop_and_save()
            s.installer.start_uninstall()
        return web.json_response(dict(ok=True,state=s.installer.state),status=202)
    if path=='system/install-core/status' and method=='GET': return ok(state=s.installer.state)
    if path=='system/stop' and method=='POST':
        async with s.lock:
            await s.ensure_switch_allowed()
            await s.stop_and_save()
        return ok()
    if path=='system/restart' and method=='POST':
        s.request_start(s.active,restart=True)
        return web.json_response(dict(ok=True,status='starting'),status=202)
    if path=='system/config':
        if method=='GET': return ok(**{k:s.cfg.get(k) for k in ['listen_host','listen_port','auth_user']})
        if method=='POST':
            async with s.lock:
                port=d.get('listen_port',s.cfg['listen_port'])
                if not 1<=int(port)<=65535: raise ValueError('端口必须在 1–65535 范围内')
                changed_user=d.get('auth_user',s.cfg['auth_user']).strip()
                if not changed_user: raise ValueError('用户名不能为空')
                password=d.get('auth_pass')
                if password and len(password)<12: raise ValueError('新密码至少需要 12 个字符')
                if password or changed_user!=s.cfg['auth_user']:
                    if not await asyncio.to_thread(check_password,d.get('current_password',''),s.cfg['auth_password_hash']): raise ValueError('当前密码不正确')
                    s.cfg['session_secret']=secrets.token_hex(32)
                if password: s.cfg['auth_password_hash']=await asyncio.to_thread(hash_password,password)
                changed=int(port)!=s.cfg['listen_port']; s.cfg.update(listen_port=int(port),auth_user=changed_user); s.persist()
            return ok(restart_required=changed)
    if path.startswith('auth/google/'):
        from oauth import google_api
        return await google_api(request,s,path,d)
    raise web.HTTPNotFound()

async def page(request):
    s=request.app['state']
    if request.path=='/__suite/floating.js': return web.FileResponse(s.static/'floating.js')
    if request.path=='/__suite/privacy.js': return web.FileResponse(s.static/'privacy.js')
    if request.path=='/__suite/proxy-editor.js': return web.FileResponse(s.static/'proxy-editor.js')
    if request.path=='/__suite/portal' or (request.path=='/' and 'portal' in request.query):
        return web.FileResponse(s.static/'portal.html',headers={'Cache-Control':'no-store'})
    if request.path.startswith('/__suite/'): raise web.HTTPNotFound()
    return await proxy(request)

async def resources(app):
    s=app['state']
    s.http=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None,sock_connect=10),
                                 auto_decompress=False,cookie_jar=aiohttp.DummyCookieJar(),trust_env=False)
    boot=None
    async def autoboot():
        try:
            target=s.cfg.get('default_profile_id')
            if target: s.request_start(target)
        except Exception as e: s.last_error=str(e); logging.error('默认档案启动失败：%s',e)
    if s.cfg.get('default_profile_id'): boot=asyncio.create_task(autoboot())
    yield
    if boot and not boot.done():
        await boot
    if s.launch_task and not s.launch_task.done(): await s.launch_task
    for task in s.detection_tasks.values():
        if not task.done(): task.cancel()
    await asyncio.gather(*s.detection_tasks.values(),return_exceptions=True)
    if s.installer.task and not s.installer.task.done():
        s.installer.task.cancel(); await asyncio.gather(s.installer.task,return_exceptions=True)
    async with s.lock:
        await s.stop_and_save()
        await asyncio.to_thread(s.supervisor.close)
    await s.http.close()
    if s.outbound_bridge: await s.outbound_bridge.close()

async def shutting_down(app):
    s=app['state']
    s.supervisor._running=False
    s.supervisor.is_restarting=True

def create_app(cfg=None,profile_mgr=None,supervisor=None):
    cfg=cfg or load_config()
    app=web.Application(middlewares=[privacy_guard,guard],client_max_size=64*1024*1024)
    app['state']=SuiteState(cfg,profile_mgr,supervisor)
    app.router.add_route('*','/__suite/api/{tail:.*}',api)
    app.router.add_route('*','/{path:.*}',page)
    app.cleanup_ctx.append(resources)
    app.on_shutdown.append(shutting_down)
    return app

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    config=load_config()
    web.run_app(create_app(config),host=config['listen_host'],port=int(config['listen_port']),access_log=None)
