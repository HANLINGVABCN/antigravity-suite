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
import socket
from pathlib import Path, PurePosixPath
from config import atomic_json
from shared_history import SharedHistory

IGNORE = {'SingletonLock', 'SingletonSocket', 'SingletonCookie', 'Crashpad'}
def safe_copytree(src, dst, exclude=()):
    root=Path(src)
    def ignore(path, names):
        return [n for n in names if (Path(path)==root and n in exclude) or n in IGNORE or n.startswith('Singleton') or 'Cache' in n
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
        from cli_credentials import CliCredentials
        self.cli_credentials=CliCredentials(self)
        if os.name=='posix':
            from cli_proxy import CliProxy
            self.cli_proxy=CliProxy(self)
        else:self.cli_proxy=None
    def _set_home(self, home):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.runtime_targets = [
            (self.home / '.gemini/jetski-standalone-oauth-token', 'jetski-standalone-oauth-token'),
            (self.home / '.config/Antigravity', 'Antigravity_config'),
            (self.home / '.gemini/antigravity', 'gemini_antigravity'),
            (self.home / '.config/antigravity', 'antigravity_credentials')]
    def home_for(self, p_id):
        if not self.get_profile(p_id):raise ValueError('档案不存在')
        spec=self.native_runtime.spec_for(p_id) or self.native_runtime.selected or (self.native_runtime.binding or {}).get('native')
        return Path(spec['native_home']) if spec else self.legacy_home

    def bind_home(self, p_id):
        home = self.home_for(p_id)
        if home.is_symlink(): raise ValueError('档案运行目录不能是符号链接')
        self._set_home(home)
        self.bound_profile = p_id
        if not self.history.location.exists():self.history.use_native(home/'.gemini/antigravity')
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
        from account_storage import TOKEN,working_file
        if relative!=TOKEN or self.bound_profile!=p_id:return None
        return working_file(self.home_for(p_id))

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
            return dict(fingerprint=hashlib.sha256(refresh.encode()).hexdigest(),email=email if isinstance(email,str) and len(email)<=320 else '',
                full_fingerprint=hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=True).encode()).hexdigest())
        except (OSError,ValueError,TypeError,AttributeError):return None

    def saved_token_metadata(self, profile_id):
        folder=self.profile_dir(profile_id)
        current=self.installed_token(folder/'store/jetski-standalone-oauth-token')
        fallback=self.installed_token(folder/'home/.gemini/jetski-standalone-oauth-token') if current is None else None
        return [value for value in (current,fallback) if value]

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
        from account_storage import TOKEN,read_bytes,write_bytes,working_file
        source=working_file(native['native_home'])
        metadata=self.installed_token(source)
        if not metadata or (expected_token and metadata['full_fingerprint']!=expected_token['full_fingerprint']):
            raise ValueError('原生登录在提取期间发生变化，请稍后重试')
        value=read_bytes(source)
        if self.installed_token(source)!=metadata:raise ValueError('原生登录正在更新，请稍后重试')
        write_bytes(self.profile_dir(profile['id'])/'store'/TOKEN,value)

    def import_installed_accounts_once(self, machine_name=None):
        marker=self.data_dir/'installed-account-import.json'
        if marker.is_file():return []
        machine_name=''.join(c for c in str(machine_name or socket.gethostname()).strip() if ord(c)>=32 and ord(c)!=127)[:128]
        default_name='默认配置-'+(machine_name or socket.gethostname())
        original=copy.deepcopy(self.profiles);imported=[]
        original_binding=copy.deepcopy(self.native_runtime.binding)
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
    def migrate_account_layout_once(self,native):
        """Upgrade old snapshots once; leave archive directories and IDs in place."""
        from account_storage import TOKEN,read_bytes,write_bytes
        marker=self.data_dir/'account-layout-v2.json'
        self.history.use_native(Path(native['native_home'])/'.gemini/antigravity')
        if marker.is_file():return False
        roots=[self.history.legacy_root,self.legacy_home/'.gemini/antigravity']
        for profile in self.profiles:
            folder=self.profile_dir(profile['id'])
            roots.extend([folder/'store/gemini_antigravity',folder/'home/.gemini/antigravity'])
            canonical=folder/'store'/TOKEN
            if not canonical.exists():
                value=read_bytes(folder/'home/.gemini'/TOKEN)
                if value is not None:write_bytes(canonical,value)
        seen={self.history.root.resolve()}
        for root in roots:
            if root.exists() and root.resolve() not in seen:
                self.history.merge_native(root);seen.add(root.resolve())
        atomic_json(marker,dict(version=2,root=str(self.history.root),completed_at=time.time()))
        return True
    def get_profile(self, p_id): return next((p for p in self.profiles if p['id']==p_id), None)
    def profile_dir(self, p_id):
        if not isinstance(p_id,str) or not re.fullmatch(r'[a-f0-9]{8,32}',p_id) or not self.get_profile(p_id):
            raise ValueError('档案不存在')
        return self.profiles_dir / p_id
    def create_profile(self, name, proxy_id='', email='', identifier=None):
        if not isinstance(name,str) or not name.strip(): raise ValueError('档案名称不能为空')
        p_id = identifier or uuid.uuid4().hex
        if not isinstance(p_id,str) or not re.fullmatch(r'[a-f0-9]{32}',p_id) or self.get_profile(p_id):raise ValueError('新配置身份无效或已存在')
        (self.profiles_dir / p_id / 'store').mkdir(parents=True, mode=0o700)
        stamp = time.strftime('%Y-%m-%d %H:%M:%S')
        item = dict(id=p_id,name=name.strip(),email=email.strip() or '未登录',proxy_id=proxy_id or '',
                    created_at=stamp,last_used_at=stamp)
        self.profiles.append(item)
        self._save()
        return item

    def renew_session_token(self, p_id, native, expected):
        binding=self.native_runtime.binding
        if binding and (binding['profile_id']!=p_id or binding['native']['id']!=native['id']):raise ValueError('不能将当前凭据写入其他登录存档')
        if self.bound_profile is not None and self.bound_profile!=p_id:raise ValueError('不能将当前凭据写入其他登录存档')
        if not self.account_matches(p_id,expected):raise ValueError('当前登录与存档不匹配')
        file=Path(native['native_home'])/'.gemini/jetski-standalone-oauth-token'
        metadata=self.installed_token(file)
        if not metadata or metadata['full_fingerprint']!=expected['full_fingerprint']:raise ValueError('凭据仍在更新，请稍后重试')
        value=json.loads(file.read_text(encoding='utf-8'))
        if hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=True).encode()).hexdigest()!=expected['full_fingerprint']:raise ValueError('凭据仍在更新，请稍后重试')
        folder=self.profile_dir(p_id)
        atomic_json(folder/'store/jetski-standalone-oauth-token',value)
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
        from account_storage import save_current
        saved=save_current(self,p_id)
        if saved:
            self.get_profile(p_id)['last_used_at']=time.strftime('%Y-%m-%d %H:%M:%S');self._save()
        return saved

    def restore_profile(self,p_id):
        from account_storage import archive_bytes,working_file,read_bytes,write_bytes
        if self.native_runtime.selected or self.native_runtime.spec_for(p_id):
            self.native_runtime.activate(p_id);self.bind_home(p_id);return True
        self.bind_home(p_id)
        target=working_file(self.home)
        value=archive_bytes(self,p_id)
        if read_bytes(target)!=value:write_bytes(target,value)
        return True

    def export_profile(self,p_id,output_path,proxy=None,include_history=False):
        from account_storage import TOKEN,archive_bytes
        profile=self.get_profile(p_id)
        metadata={key:profile.get(key) for key in ('id','name','email','proxy_id','created_at','last_used_at')}
        metadata['format_version']=2
        metadata['export_options']=dict(include_history=bool(include_history),include_proxy=proxy is not None)
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);store=folder/'store';store.mkdir()
            value=archive_bytes(self,p_id)
            if value is not None:(store/TOKEN).write_bytes(value);os.chmod(store/TOKEN,0o600)
            atomic_json(folder/'profile.json',metadata)
            with tarfile.open(output_path,'w:gz') as archive:
                archive.add(store,arcname='profile/store')
                archive.add(folder/'profile.json',arcname='profile/profile.json')
                if proxy is not None:
                    atomic_json(folder/'proxy.json',proxy);archive.add(folder/'proxy.json',arcname='profile/proxy.json')
                if include_history:
                    for name in ('conversations','brain'):
                        archive.add(self.history.root/name,arcname='profile/shared-history/'+name)
                    snapshot=folder/'conversation_summaries.db'
                    if self.history.backup_database(snapshot):archive.add(snapshot,arcname='profile/shared-history/'+snapshot.name)
        return output_path

    @staticmethod
    def checked_import_members(tar):
        total=count=0
        for member in tar:
            count+=1;total+=member.size
            parts=PurePosixPath(member.name).parts
            if (not parts or member.name.startswith('/') or '..' in parts or '\\' in member.name
                or ':' in member.name or not (member.isfile() or member.isdir())
                or count>100000 or total>1024*1024*1024):
                raise ValueError('压缩包包含非法路径、链接或超过大小限制')
            yield member,parts

    def _extract_import(self,tar_path,root):
        root=Path(root)
        with tarfile.open(tar_path,'r:gz') as tar:
            for member,parts in self.checked_import_members(tar):
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
            ProxyManager.validate_fields(**{key:proxy.get(key,'') for key in ('name','protocol','host','port','username','password')},allow_legacy=True)
        return source,meta,proxy

    def inspect_import(self,tar_path):
        # Preview validates the full archive but only reads small metadata files.
        # Materializing every application file twice is expensive on small VPS disks.
        metadata={};proxy_files={};store_roots=set();store_names={};history_files=0;history_bytes=0
        with tarfile.open(tar_path,'r:gz') as tar:
            for member,parts in self.checked_import_members(tar):
                prefixes=((parts[0],'shared-history'),(parts[0],'store','gemini_antigravity'),(parts[0],'home','.gemini','antigravity'))
                if member.isfile() and any(parts[:len(prefix)]==prefix and len(parts)>len(prefix) and
                    (parts[len(prefix)] in ('conversations','brain') or parts[len(prefix)]=='conversation_summaries.db') for prefix in prefixes):
                    history_files+=1;history_bytes+=member.size
                if len(parts)>=2 and parts[1]=='store':
                    store_roots.add(parts[0])
                    if len(parts)>=3:store_names.setdefault(parts[0],set()).add(parts[2])
                if len(parts)==2 and parts[1] in ('profile.json','proxy.json'):
                    if not member.isfile() or member.size>65536:raise ValueError('档案或代理元数据格式无效或过大')
                    value=json.loads(tar.extractfile(member).read().decode('utf-8'))
                    if not isinstance(value,dict):raise ValueError('档案或代理元数据格式无效')
                    (metadata if parts[1]=='profile.json' else proxy_files)[parts[0]]=value
        if len(metadata)!=1:raise ValueError('需要包含单个 profile.json 和 store 目录的档案包')
        root,meta=next(iter(metadata.items()))
        if root not in store_roots:raise ValueError('档案包缺少 store 目录')
        if store_names.get(root,set())-{name for _,name in self.runtime_targets}:raise ValueError('未知运行目录')
        proxy=proxy_files.get(root)
        if proxy is not None:
            from proxy_manager import ProxyManager
            ProxyManager.validate_fields(**{key:proxy.get(key,'') for key in ('name','protocol','host','port','username','password')},allow_legacy=True)
        return dict(name=str(meta.get('name','导入配置')),has_proxy=proxy is not None,has_history=bool(history_files),history_files=history_files,history_bytes=history_bytes,history_message='包含聊天文件' if history_files else '无聊天文件',
                    proxy={key:ProxyManager.normalize_protocol(proxy.get(key)) if key=='protocol' else proxy.get(key) for key in ('name','protocol','host','port')} if proxy is not None else None,
                    proxy_message='包含对应代理' if proxy is not None else '无代理文件')

    def import_profile(self,tar_path,proxy_manager=None,include_proxy=False,include_history=False,history_mode='merge'):
        from account_storage import TOKEN,read_bytes,write_bytes
        if not isinstance(include_history,bool):raise ValueError('是否导入聊天记录必须为布尔值')
        if history_mode not in ('merge','replace'):raise ValueError('聊天导入方式无效')
        with tempfile.TemporaryDirectory(dir=self.profiles_dir,prefix='.import-') as temporary:
            source,meta,proxy=self._extract_import(tar_path,temporary)
            roots=[source/'store/gemini_antigravity',source/'home/.gemini/antigravity',source/'shared-history']
            has_history=any(self.has_history(root) for root in roots)
            if include_history and not has_history:raise ValueError('无聊天文件，不能导入聊天记录')
            original_profiles=copy.deepcopy(self.profiles)
            original_nodes=copy.deepcopy(proxy_manager.proxies) if proxy_manager is not None else None
            profile=None;transfer=None
            try:
                if include_history:transfer=self.history.prepare_import(roots,history_mode)
                proxy_id=''
                if include_proxy and proxy is not None:
                    if proxy_manager is None:raise ValueError('代理管理器不可用')
                    proxy_id=proxy_manager.import_proxy(proxy)['id']
                profile=self.create_profile(str(meta.get('name','导入配置'))+' (副本)',proxy_id,str(meta.get('email','')))
                credential=read_bytes(source/'store'/TOKEN)
                if credential is None:credential=read_bytes(source/'home/.gemini'/TOKEN)
                if credential is not None:write_bytes(self.profile_dir(profile['id'])/'store'/TOKEN,credential)
                if transfer:transfer.apply();transfer.commit()
                return profile
            except Exception:
                if profile is not None:remove_path(self.profiles_dir/profile['id'])
                self.profiles=original_profiles;self._save()
                if proxy_manager is not None:
                    proxy_manager.proxies=original_nodes;proxy_manager._save()
                raise
            finally:
                if transfer:transfer.close()

    @staticmethod
    def has_history(root):
        root=Path(root)
        return (root/'conversation_summaries.db').is_file() or any(p.is_file() for n in ('conversations','brain') for p in (root/n).rglob('*'))

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
