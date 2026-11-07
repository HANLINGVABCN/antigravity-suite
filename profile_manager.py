import copy
import json
import os
import re
import shutil
import tarfile
import tempfile
import time
import uuid
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
    def _set_home(self, home):
        self.home = Path(home)
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.runtime_targets = [
            (self.home / '.gemini/jetski-standalone-oauth-token', 'jetski-standalone-oauth-token'),
            (self.home / '.config/Antigravity', 'Antigravity_config'),
            (self.home / '.gemini/antigravity', 'gemini_antigravity'),
            (self.home / '.config/antigravity', 'antigravity_credentials')]
    def home_for(self, p_id):
        return self.profile_dir(p_id) / 'home'
    def bind_home(self, p_id):
        home = self.home_for(p_id)
        if home.is_symlink(): raise ValueError('档案运行目录不能是符号链接')
        self._set_home(home)
        self.bound_profile = p_id
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
    def list_profiles(self): return copy.deepcopy(self.profiles)
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
        remove_path(self.profile_dir(p_id))
        self.profiles=[p for p in self.profiles if p['id']!=p_id]
        self._save()
    def save_active_to_profile(self,p_id):
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
            return True
        finally: remove_path(stage)
    def restore_profile(self,p_id):
        store=self.profile_dir(p_id)/'store'
        if not store.is_dir(): raise ValueError('档案存储目录不存在')
        self.bind_home(p_id)
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
        atomic_json(folder/'profile.json',self.get_profile(p_id))
        with tarfile.open(output_path,'w:gz') as tar:
            tar.add(folder/'store',arcname='profile/store')
            tar.add(folder/'profile.json',arcname='profile/profile.json')
            if proxy is not None:
                # A snapshot of only this profile's associated node, never the pool.
                with tempfile.TemporaryDirectory() as tmp:
                    node=Path(tmp)/'proxy.json'
                    atomic_json(node,proxy)
                    tar.add(node,arcname='profile/proxy.json')
            home=folder/'home'
            if home.is_dir():
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
