import copy
import json
import os
import re
import shutil
import tarfile
import tempfile
import time
import uuid
import hashlib
import base64
import sqlite3
import socket
from pathlib import Path, PurePosixPath
from config import atomic_json
from shared_history import SharedHistory

IGNORE = {'SingletonLock', 'SingletonSocket', 'SingletonCookie', 'Crashpad'}
def safe_copytree(src, dst):
    def ignore(path, names):
        return [n for n in names if n in IGNORE or n.startswith('Singleton') or 'Cache' in n
                or Path(path,n).is_symlink() or not (Path(path,n).is_dir() or Path(path,n).is_file())]
    shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=True)

def remove_path(path):
    path = Path(path)
    if path.is_symlink() or path.is_file(): path.unlink()
    elif path.exists(): shutil.rmtree(path)

class ProfileManager:
    def __init__(self, data_dir, profiles_dir, runtime_home=None):
        self.data_dir, self.profiles_dir = Path(data_dir), Path(profiles_dir)
        self.profiles_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.meta_file = self.data_dir / 'profiles.json'
        self.profiles = json.loads(self.meta_file.read_text(encoding='utf-8')) if self.meta_file.exists() else []
        self.legacy_home = Path(runtime_home or self.data_dir / 'runtime-home')
        self.home = self.legacy_home
        self.bound_profile = None
        self.history=SharedHistory(self.data_dir)
        self._set_home(self.home)
        from native_runtime import NativeRuntime
        self.native_runtime=NativeRuntime(self)
    def _set_home(self, home):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.runtime_targets = [
            (self.home / '.gemini/jetski-standalone-oauth-token', 'jetski-standalone-oauth-token'),
            (self.home / '.config/Antigravity', 'Antigravity_config'),
            (self.home / '.gemini/antigravity', 'gemini_antigravity'),
            (self.home / '.config/antigravity', 'antigravity_credentials')]
    def home_for(self, p_id):
        profile=self.get_profile(p_id)
        spec=self.native_runtime.spec_for(p_id)
        if spec:return Path(spec['native_home'])
        return self.profile_dir(p_id) / 'home'
    def bind_home(self, p_id):
        home = self.home_for(p_id)
        if home.is_symlink(): raise ValueError('档案运行目录不能是符号链接')
        self._set_home(home)
        self.bound_profile = p_id
        profile=self.get_profile(p_id)
        spec=self.native_runtime.spec_for(p_id)
        if spec:
            self.runtime_targets=[(home/'.gemini/jetski-standalone-oauth-token','jetski-standalone-oauth-token'),
                (Path(spec['native_config_dir']),'Antigravity_config'),(home/'.gemini/antigravity','gemini_antigravity'),
                (home/'.config/antigravity','antigravity_credentials')]
    def register_existing(self,spec):
        key=str(Path(spec['home']).resolve())+'\0'+str(Path(spec['config_dir']).resolve())
        identifier=hashlib.sha256(key.encode()).hexdigest()[:32]
        item=self.get_profile(identifier)
        if item and item.get('kind')!='existing':raise ValueError('已有配置 ID 与普通配置冲突')
        if item is None:
            stamp=time.strftime('%Y-%m-%d %H:%M:%S')
            item=dict(id=identifier,name='已有共享配置 · '+Path(spec['home']).name,email='未登录',proxy_id='',
                created_at=stamp,last_used_at=stamp,kind='existing')
            self.profiles.append(item)
            (self.profiles_dir/identifier/'store').mkdir(parents=True,exist_ok=True,mode=0o700)
        updates=dict(native_home=spec['home'],native_config_dir=spec['config_dir'],native_uid=spec['uid'],
            native_binary=spec['binary'],native_display=spec['display'],native_env=spec.get('session_env',{}),available=spec['available'])
        changed=any(item.get(k)!=v for k,v in updates.items())
        item.update(updates)
        if changed:self._save()
        self.refresh_existing_metadata(identifier)
        return item
    def refresh_existing_metadata(self,p_id):
        item=self.get_profile(p_id)
        if not item or item.get('kind')!='existing':return
        token=Path(item['native_home'])/'.gemini/jetski-standalone-oauth-token'
        email='未登录';present=False
        try:
            if token.is_file() and token.stat().st_size<=1024*1024:
                value=json.loads(token.read_text(encoding='utf-8'))
                if isinstance(value,dict):
                    present=bool(value.get('token',{}).get('refresh_token'))
                    candidate=value.get('id_token_email')
                    if not candidate and isinstance(value.get('id_token'),str):
                        try:
                            encoded=value['id_token'].split('.')[1]
                            payload=json.loads(base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)))
                            candidate=payload.get('email')
                        except (ValueError,IndexError,TypeError):pass
                    email=candidate if isinstance(candidate,str) and len(candidate)<=320 else '未登录'
        except (OSError,ValueError,TypeError,AttributeError):pass
        if item.get('email')!=email or item.get('token_present')!=present:
            item.update(email=email,token_present=present);self._save()
    def file_in_home(self,p_id,relative):
        parts=PurePosixPath(relative).parts
        roots={'jetski-standalone-oauth-token':'.gemini/jetski-standalone-oauth-token','Antigravity_config':'.config/Antigravity','gemini_antigravity':'.gemini/antigravity','antigravity_credentials':'.config/antigravity'}
        if not parts or parts[0] not in roots:return None
        home=self.home_for(p_id)
        path=home/roots[parts[0]]
        if len(parts)>1:path=path.joinpath(*parts[1:])
        if not path.resolve().is_relative_to(home.resolve()):raise ValueError('共享聊天记录不能通过档案文件编辑器覆盖')
        return path
    def _save(self): atomic_json(self.meta_file, self.profiles)
    def list_profiles(self): return copy.deepcopy([p for p in self.profiles if not p.get('internal_runtime') and not p.get('pending_native_import')])

    @staticmethod
    def installed_token(file):
        try:
            file=Path(file)
            if not file.is_file() or file.stat().st_size>1024*1024:return None
            value=json.loads(file.read_text(encoding='utf-8'))
            refresh=value.get('token',{}).get('refresh_token')
            if not isinstance(refresh,str) or not refresh:return None
            email=value.get('id_token_email','')
            if not email and isinstance(value.get('id_token'),str):
                try:
                    part=value['id_token'].split('.')[1]
                    email=json.loads(base64.urlsafe_b64decode(part+'='*(-len(part)%4))).get('email','')
                except (ValueError,IndexError,TypeError):pass
            return dict(fingerprint=hashlib.sha256(refresh.encode()).hexdigest(),email=email if isinstance(email,str) and len(email)<=320 else '')
        except (OSError,ValueError,TypeError,AttributeError):return None

    def saved_token_metadata(self, profile_id):
        folder=self.profile_dir(profile_id)
        return [value for value in (self.installed_token(folder/'store/jetski-standalone-oauth-token'),
            self.installed_token(folder/'home/.gemini/jetski-standalone-oauth-token')) if value]

    def account_matches(self, profile_id, token):
        if not token:return False
        profile=self.get_profile(profile_id)
        saved=self.saved_token_metadata(profile_id)
        if any(value['fingerprint']==token['fingerprint'] for value in saved):return True
        emails=[profile.get('email','')]+[value['email'] for value in saved]
        return bool(token['email'] and any(value.lower()==token['email'].lower() for value in emails if value))

    def find_saved_account(self, token, preferred_id=None):
        candidates=[p for p in self.profiles if not p.get('internal_runtime') and not p.get('pending_native_import') and p.get('kind')!='existing']
        candidates.sort(key=lambda p:p['id']!=preferred_id)
        return next((p for p in candidates if self.account_matches(p['id'],token)),None)

    def capture_installed_account(self, profile, native, expected_token=None):
        from native_runtime import targets
        folder=self.profile_dir(profile['id'])
        stage=Path(tempfile.mkdtemp(prefix='.installed-account-',dir=folder))
        backup=None
        try:
            for name,source in targets(native['native_home'],native['native_config_dir']).items():
                target=stage/name
                if source.is_dir():
                    safe_copytree(source,target)
                    # Capture committed SQLite/WAL state if the desktop app is still running.
                    for file in target.rglob('*'):
                        if not file.is_file():continue
                        with file.open('rb') as stream:header=stream.read(16)
                        if header!=b'SQLite format 3\0':continue
                        original=source/file.relative_to(target)
                        fd,snapshot=tempfile.mkstemp(prefix='.sqlite-import-',dir=self.data_dir)
                        os.close(fd);saved=Path(snapshot)
                        incoming=outgoing=None
                        try:
                            incoming=sqlite3.connect('file:'+original.as_posix()+'?mode=ro',uri=True,timeout=3)
                            outgoing=sqlite3.connect(saved)
                            incoming.backup(outgoing)
                            incoming.close();outgoing.close()
                            os.replace(saved,file)
                        finally:
                            if incoming:incoming.close()
                            if outgoing:outgoing.close()
                            saved.unlink(missing_ok=True)
                        for suffix in ('-wal','-shm','-journal'):
                            (file.parent/(file.name+suffix)).unlink(missing_ok=True)
                elif source.is_file():shutil.copy2(source,target)
            if expected_token:
                captured=self.installed_token(stage/'jetski-standalone-oauth-token')
                if not captured or captured['fingerprint']!=expected_token['fingerprint']:
                    raise ValueError('原生登录在提取期间发生变化，请稍后重试')
            old=folder/'store'
            if old.exists():
                backup=self.data_dir/'installed-account-backups'/str(time.time_ns())/profile['id']
                backup.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                os.replace(old,backup)
            os.replace(stage,old)
            self.history.merge_native(Path(native['native_home'])/'.gemini/antigravity')
        except Exception:
            if backup and backup.exists():
                remove_path(folder/'store');os.replace(backup,folder/'store')
            raise
        finally:
            if stage.exists():shutil.rmtree(stage)

    def import_installed_accounts_once(self, machine_name=None):
        marker=self.data_dir/'installed-account-import.json'
        if marker.is_file():return []
        machine_name=''.join(c for c in str(machine_name or socket.gethostname()).strip() if ord(c)>=32 and ord(c)!=127)[:128]
        default_name='默认配置-'+(machine_name or socket.gethostname())
        original=copy.deepcopy(self.profiles);imported=[]
        original_binding=copy.deepcopy(self.native_runtime.binding)
        atomic_json(self.data_dir/'installed-account-profile-backup.json',original)
        try:
            for native in self.native_runtime.installations:
                token=self.installed_token(Path(native['native_home'])/'.gemini/jetski-standalone-oauth-token')
                if not token:continue
                candidates=[p for p in self.profiles if p.get('kind')!='existing' and not p.get('internal_runtime')]
                binding=self.native_runtime.binding
                candidates.sort(key=lambda p: p['id']!=(binding or {}).get('profile_id'))
                match=None
                if not match:
                    for candidate in candidates:
                        files=[self.profile_dir(candidate['id'])/'store/jetski-standalone-oauth-token',
                               self.profile_dir(candidate['id'])/'home/.gemini/jetski-standalone-oauth-token']
                        found=[self.installed_token(file) for file in files]
                        if any(item and item['fingerprint']==token['fingerprint'] for item in found):match=candidate;break
                if not match and token['email']:
                    match=next((p for p in candidates if p.get('email','').lower()==token['email'].lower()),None)
                if not match:
                    legacy=next((p for p in self.profiles if p.get('kind')=='existing' and p['id']==native['id']),None)
                    if legacy:
                        match=legacy
                        for key in list(match):
                            if key=='kind' or key.startswith('native_') or key in ('available','token_present','internal_runtime'):match.pop(key)
                        if match['name'].startswith('已有共享配置'):
                            match['name']=default_name
                        match['email']=token['email'] or match.get('email','未登录')
                    else:match=self.create_profile(default_name,email=token['email'])
                    self.capture_installed_account(match,native)
                imported.append(match['id'])
                if binding is None and len(self.native_runtime.installations)==1:
                    self.native_runtime.binding=dict(profile_id=match['id'],native=dict(native))
                    atomic_json(self.native_runtime.file,self.native_runtime.binding)
            # Preserve legacy IDs and files privately; do not turn a runtime directory into a second account.
            for profile in self.profiles:
                if profile.get('kind')=='existing':profile['internal_runtime']=True
            self._save()
            atomic_json(marker,dict(completed=True,imported_profile_ids=imported,machine_name=machine_name,imported_at=time.time()))
            return imported
        except Exception:
            self.profiles=original;self._save()
            self.native_runtime.binding=original_binding
            if original_binding:atomic_json(self.native_runtime.file,original_binding)
            else:self.native_runtime.file.unlink(missing_ok=True)
            raise
    def get_profile(self, p_id): return next((p for p in self.profiles if p['id']==p_id), None)
    def profile_dir(self, p_id):
        if not isinstance(p_id,str) or not re.fullmatch(r'[a-f0-9]{8,32}',p_id) or not self.get_profile(p_id):
            raise ValueError('档案不存在')
        return self.profiles_dir / p_id
    def create_profile(self, name, proxy_id='', email=''):
        if not isinstance(name,str) or not name.strip(): raise ValueError('档案名称不能为空')
        p_id = uuid.uuid4().hex
        (self.profiles_dir / p_id / 'store').mkdir(parents=True, mode=0o700)
        stamp = time.strftime('%Y-%m-%d %H:%M:%S')
        item = dict(id=p_id,name=name.strip(),email=email.strip() or '未登录',proxy_id=proxy_id or '',
                    created_at=stamp,last_used_at=stamp)
        self.profiles.append(item)
        self._save()
        return item
    def update_profile(self,p_id,name=None,proxy_id=None,email=None):
        p=self.get_profile(p_id)
        if not p: raise ValueError('档案不存在')
        updated=copy.deepcopy(p)
        if name is not None:
            if not isinstance(name,str) or not name.strip(): raise ValueError('档案名称不能为空')
            updated['name']=name.strip()
        if proxy_id is not None:
            if not isinstance(proxy_id,str):raise ValueError('代理绑定 ID 无效')
            updated['proxy_id']=proxy_id
        if email is not None:
            if not isinstance(email,str):raise ValueError('邮箱必须是文本')
            updated['email']=email.strip()
        original=copy.deepcopy(p)
        p.clear();p.update(updated)
        try:self._save()
        except Exception:
            p.clear();p.update(original)
            raise
        return p
    def delete_profile(self,p_id):
        if self.get_profile(p_id) and self.get_profile(p_id).get('kind')=='existing':raise ValueError('已有共享配置不能删除，原数据由现有反重力使用')
        remove_path(self.profile_dir(p_id))
        self.profiles=[p for p in self.profiles if p['id']!=p_id]
        self._save()
        if self.native_runtime.binding and self.native_runtime.binding['profile_id']==p_id:
            self.native_runtime.ignore_current_login()
            self.native_runtime.binding=None
            self.native_runtime.file.unlink(missing_ok=True)
    def save_active_to_profile(self,p_id):
        profile=self.get_profile(p_id)
        if profile and profile.get('kind')!='existing' and self.bound_profile is not None and self.bound_profile!=p_id:return False
        native=self.native_runtime.spec_for(p_id)
        if not native and profile and profile.get('kind')!='existing':
            native=next((p for p in self.native_runtime.installations if Path(p['native_home']).resolve()==self.home.resolve()),None)
        if native and profile and profile.get('kind')!='existing':
            current=self.installed_token(Path(native['native_home'])/'.gemini/jetski-standalone-oauth-token')
            if not self.account_matches(p_id,current):return False
        if profile and profile.get('kind')=='existing':
            # Snapshot credentials but never overwrite the native runtime during restore.
            self.bind_home(p_id)
            self.history.merge_native(Path(profile['native_home'])/'.gemini/antigravity')
        folder=self.profile_dir(p_id)
        stage=Path(tempfile.mkdtemp(prefix='.snapshot-',dir=folder))
        old=folder/'.previous-store'
        try:
            for src,name in self.runtime_targets:
                if src.is_symlink(): raise ValueError('运行目录不能是符号链接')
                if src.is_dir(): safe_copytree(src,stage/name)
                elif src.is_file(): shutil.copy2(src,stage/name)
            remove_path(old)
            if (folder/'store').exists(): os.replace(folder/'store',old)
            try: os.replace(stage,folder/'store')
            except Exception:
                if old.exists(): os.replace(old,folder/'store')
                raise
            remove_path(old)
            self.get_profile(p_id)['last_used_at']=time.strftime('%Y-%m-%d %H:%M:%S')
            self._save()
            self.native_runtime.snapshot_to_private(p_id)
            return True
        finally: remove_path(stage)
    def restore_profile(self,p_id):
        store=self.profile_dir(p_id)/'store'
        if not store.is_dir(): raise ValueError('档案存储目录不存在')
        self.bind_home(p_id)
        profile=self.get_profile(p_id)
        spec=self.native_runtime.spec_for(p_id)
        if spec and profile.get('kind')!='existing':
            self.history.sync_native(self.home/'.gemini/antigravity',spec['native_uid'])
            return True
        if profile.get('kind')=='existing':
            if not profile.get('available'):raise ValueError('已有配置当前不可接管，请检查本体和目录权限')
            self.history.sync_native(self.home/'.gemini/antigravity',profile['native_uid'])
            self.refresh_existing_metadata(p_id)
            return True
        marker=self.home/'.suite-home-initialized'
        if marker.exists():
            # The persistent per-profile HOME is authoritative after migration.
            # Do not replace conversations with an older credential snapshot.
            self.history.attach(self.home)
            return True
        with tempfile.TemporaryDirectory(dir=self.home,prefix='.restore-') as tmp:
            staged=Path(tmp)/'new'; staged.mkdir()
            backup=Path(tmp)/'old'; backup.mkdir()
            for _,name in self.runtime_targets:
                src=store/name
                if src.is_symlink(): raise ValueError('档案不能包含符号链接')
                if src.is_dir(): safe_copytree(src,staged/name)
                elif src.is_file(): shutil.copy2(src,staged/name)
            changed=[]
            try:
                for dest,name in self.runtime_targets:
                    dest.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                    if dest.exists() or dest.is_symlink(): os.replace(dest,backup/name)
                    changed.append((dest,name))
                    if (staged/name).exists(): os.replace(staged/name,dest)
            except Exception:
                for dest,name in reversed(changed):
                    remove_path(dest)
                    if (backup/name).exists() or (backup/name).is_symlink(): os.replace(backup/name,dest)
                raise
        self.get_profile(p_id)['last_used_at']=time.strftime('%Y-%m-%d %H:%M:%S')
        self._save()
        marker.write_text('1',encoding='ascii')
        self.history.attach(self.home)
        return True
    def export_profile(self,p_id,output_path,proxy=None):
        folder=self.profile_dir(p_id)
        profile=self.get_profile(p_id)
        metadata={key:profile.get(key) for key in ('id','name','email','proxy_id','created_at','last_used_at')}
        atomic_json(folder/'profile.json',metadata)
        with tarfile.open(output_path,'w:gz') as tar:
            tar.add(folder/'store',arcname='profile/store')
            tar.add(folder/'profile.json',arcname='profile/profile.json')
            if proxy is not None:
                # A snapshot of only this profile's associated node, never the pool.
                with tempfile.TemporaryDirectory() as tmp:
                    node=Path(tmp)/'proxy.json'
                    atomic_json(node,proxy)
                    tar.add(node,arcname='profile/proxy.json')
            home=folder/'home' if profile.get('kind')!='existing' else None
            if home is not None and home.is_dir():
                def filter_member(info):
                    parts=PurePosixPath(info.name).parts
                    if any(n in IGNORE or n.startswith('Singleton') or 'Cache' in n or n in ('.tmp','.cache','engine.log') for n in parts):return None
                    return info if info.isfile() or info.isdir() else None
                tar.add(home,arcname='profile/home',filter=filter_member)
            if self.history.root.exists():
                tar.add(self.history.root/'conversations',arcname='profile/shared-history/conversations')
                tar.add(self.history.root/'brain',arcname='profile/shared-history/brain')
                db=self.history.root/'conversation_summaries.db'
                if db.is_file():
                    with tempfile.TemporaryDirectory() as tmp:
                        snapshot=Path(tmp)/db.name
                        self.history.backup_database(snapshot)
                        tar.add(snapshot,arcname='profile/shared-history/conversation_summaries.db')
        return output_path
    def _extract_import(self,tar_path,root):
        root=Path(root); total=count=0
        with tarfile.open(tar_path,'r:gz') as tar:
            for member in tar:
                count+=1; total+=member.size
                parts=PurePosixPath(member.name).parts
                if (not parts or member.name.startswith('/') or '..' in parts or '\\' in member.name
                    or ':' in member.name or not (member.isfile() or member.isdir())
                    or count>20000 or total>256*1024*1024):
                    raise ValueError('压缩包包含非法路径、链接或超过大小限制')
                dest=root.joinpath(*parts)
                if member.isdir(): dest.mkdir(parents=True,exist_ok=True)
                else:
                    dest.parent.mkdir(parents=True,exist_ok=True)
                    with tar.extractfile(member) as src,dest.open('wb') as out: shutil.copyfileobj(src,out)
                    os.chmod(dest,0o600)
        metas=list(root.glob('*/profile.json'))
        if len(metas)!=1: raise ValueError('需要包含单个 profile.json 和 store 目录的档案包')
        source=metas[0].parent
        if metas[0].stat().st_size>65536:raise ValueError('档案元数据文件过大')
        meta=json.loads(metas[0].read_text(encoding='utf-8'))
        if not isinstance(meta,dict):raise ValueError('档案元数据格式无效')
        if not (source/'store').is_dir(): raise ValueError('档案包缺少 store 目录')
        allowed={name for _,name in self.runtime_targets}
        if any(p.name not in allowed for p in (source/'store').iterdir()): raise ValueError('未知运行目录')
        proxy=None
        proxy_file=source/'proxy.json'
        if proxy_file.exists():
            if not proxy_file.is_file() or proxy_file.stat().st_size>65536:raise ValueError('代理文件格式无效或过大')
            proxy=json.loads(proxy_file.read_text(encoding='utf-8'))
            from proxy_manager import ProxyManager
            if not isinstance(proxy,dict):raise ValueError('代理文件格式无效')
            ProxyManager.validate_fields(**{key:proxy.get(key,'') for key in ('name','protocol','host','port','username','password')})
        return source,meta,proxy

    def inspect_import(self,tar_path):
        with tempfile.TemporaryDirectory(dir=self.profiles_dir,prefix='.inspect-') as tmp:
            _,meta,proxy=self._extract_import(tar_path,tmp)
            return dict(name=str(meta.get('name','导入配置')),has_proxy=proxy is not None,
                        proxy={key:proxy.get(key) for key in ('name','protocol','host','port')} if proxy is not None else None,
                        proxy_message='包含对应代理' if proxy is not None else '无代理文件')

    def import_profile(self,tar_path,proxy_manager=None,include_proxy=False):
        with tempfile.TemporaryDirectory(dir=self.profiles_dir,prefix='.import-') as tmp:
            source,meta,proxy=self._extract_import(tar_path,tmp)
            original_profiles=copy.deepcopy(self.profiles)
            original_nodes=copy.deepcopy(proxy_manager.proxies) if proxy_manager is not None else None
            p=None
            try:
                proxy_id=''
                if include_proxy and proxy is not None:
                    if proxy_manager is None:raise ValueError('代理管理器不可用')
                    proxy_id=proxy_manager.import_proxy(proxy)['id']
                p=self.create_profile(str(meta.get('name','导入配置'))+' (副本)',proxy_id,str(meta.get('email','')))
                dest=self.profile_dir(p['id'])/'store'; remove_path(dest)
                shutil.move(str(source/'store'),str(dest))
                if (source/'home').is_dir(): shutil.move(str(source/'home'),str(self.profile_dir(p['id'])/'home'))
                if (source/'shared-history').is_dir():self.history.merge_native(source/'shared-history')
                return p
            except Exception:
                if p is not None:remove_path(self.profiles_dir/p['id'])
                self.profiles=original_profiles;self._save()
                if proxy_manager is not None:
                    proxy_manager.proxies=original_nodes;proxy_manager._save()
                raise
    def resolve_file(self,p_id,rel):
        root=(self.profile_dir(p_id)/'store').resolve()
        parts=PurePosixPath(rel).parts
        if not parts or rel.startswith('/') or '..' in parts or '\\' in rel or ':' in rel: raise ValueError('文件路径无效')
        path=root.joinpath(*parts)
        if any(p.is_symlink() for p in [path]+list(path.parents) if p!=root.parent):
            raise ValueError('不能编辑符号链接')
        try: path.resolve().relative_to(root)
        except ValueError: raise ValueError('文件必须位于当前档案内')
        return path
