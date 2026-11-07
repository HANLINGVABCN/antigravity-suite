"""A single existing runtime shared by desktop and the currently selected account."""
import json
import hashlib
import os
import shutil
import time
from pathlib import Path

from config import atomic_json


NAMES = ('jetski-standalone-oauth-token', 'Antigravity_config', 'gemini_antigravity', 'antigravity_credentials')


def targets(home, config_dir=None):
    home=Path(home)
    return dict(zip(NAMES,(home/'.gemini/jetski-standalone-oauth-token',
        Path(config_dir) if config_dir else home/'.config/Antigravity',
        home/'.gemini/antigravity',home/'.config/antigravity')))


def own_tree(path, uid):
    if not hasattr(os,'chown'):
        return
    import pwd
    gid=pwd.getpwuid(uid).pw_gid
    for item in [Path(path)]+list(Path(path).rglob('*')) if Path(path).is_dir() else [Path(path)]:
        if not item.is_symlink():
            os.chown(item,uid,gid)


class NativeRuntime:
    def __init__(self, manager):
        self.manager=manager
        self.file=manager.data_dir/'shared-native-runtime.json'
        self.binding=json.loads(self.file.read_text(encoding='utf-8')) if self.file.is_file() else None
        self.registry_file=manager.data_dir/'native-installations.json'
        self.installations=json.loads(self.registry_file.read_text(encoding='utf-8')) if self.registry_file.is_file() else []
        self.selected=None
        if self.binding:
            self.validate(self.binding['native'])
            if not manager.get_profile(self.binding['profile_id']):
                raise ValueError('共用运行配置的账号不存在，请恢复私有备份')

    def validate(self, spec):
        data=self.manager.data_dir.resolve()
        for name in ('native_home','native_config_dir'):
            path=Path(spec[name]).resolve()
            if not path.is_absolute() or path==data or path.is_relative_to(data) or len(path.parts)<2:
                raise ValueError('已有共用配置必须位于私有档案目录之外')
        if not isinstance(spec.get('native_uid'),int):
            raise ValueError('共用配置所属用户无效')

    def register_installation(self, spec):
        key=str(Path(spec['home']).resolve())+'\0'+str(Path(spec['config_dir']).resolve())
        identifier=hashlib.sha256(key.encode()).hexdigest()[:32]
        value=dict(id=identifier,native_home=spec['home'],native_config_dir=spec['config_dir'],native_uid=spec['uid'],
            native_binary=spec['binary'],native_display=spec['display'],native_env=spec.get('session_env',{}),available=spec['available'])
        self.validate(value)
        item=next((p for p in self.installations if p['id']==identifier),None)
        if item is None:self.installations.append(value);item=value
        elif item!=value:item.clear();item.update(value)
        else:return item
        atomic_json(self.registry_file,self.installations)
        return item

    def refresh_installations(self, specs):
        seen=set()
        for spec in specs:seen.add(self.register_installation(spec)['id'])
        changed=False
        for item in self.installations:
            if item['id'] not in seen and item.get('available'):
                item['available']=False;changed=True
        if changed:atomic_json(self.registry_file,self.installations)
        self.select()

    def select(self, profiles=None):
        # Old records remain readable for migration, but runtime discovery creates no account cards.
        available=[p for p in self.installations if p.get('available')]
        if not self.installations and profiles is not None:
            available=[p for p in profiles if p.get('kind')=='existing' and p.get('available')]
        if self.binding:
            self.selected=next((p for p in available if p['id']==self.binding['native']['id']),None)
        elif len(available)==1:
            self.selected=available[0]
        else:
            self.selected=None
        return self.selected

    def spec_for(self, profile_id):
        if self.binding and self.binding['profile_id']==profile_id:
            return self.binding['native']
        profile=self.manager.get_profile(profile_id)
        return profile if profile and profile.get('kind')=='existing' else None

    def resolve(self, profile_id):
        if self.binding and profile_id==self.binding['native']['id']:
            return self.binding['profile_id']
        return profile_id

    def activate(self, profile_id):
        from profile_manager import safe_copytree,remove_path
        profile=self.manager.get_profile(profile_id)
        spec=profile if profile and profile.get('kind')=='existing' else self.selected
        if not spec:
            return False
        self.validate(spec)
        if not spec.get('available'):
            raise ValueError('原共用配置当前不可用，请检查本体和目录权限')
        if self.binding and self.binding['profile_id']==profile_id and self.binding['native']['id']==spec['id']:
            return True
        home=Path(spec['native_home']);config=Path(spec['native_config_dir'])
        self.manager.history.merge_native(home/'.gemini/antigravity')
        dest=targets(home,config)
        backup=self.manager.data_dir/'native-runtime-backups'/str(time.time_ns())
        backup.mkdir(parents=True,mode=0o700)
        atomic_json(backup/'binding.json',self.binding or {})
        for name,path in dest.items():
            if path.is_symlink():
                raise ValueError('原共用配置路径不能是符号链接')
            if path.is_dir():safe_copytree(path,backup/name)
            elif path.is_file():shutil.copy2(path,backup/name)
        if profile.get('kind')!='existing':
            private=self.manager.profile_dir(profile_id)/'home'
            source=targets(private) if (private/'.suite-home-initialized').is_file() else {
                name:self.manager.profile_dir(profile_id)/'store'/name for name in NAMES}
            changed=[]
            try:
                for name,path in dest.items():
                    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                    if hasattr(os,'chown') and path.parent in (home/'.gemini',home/'.config'):
                        import pwd
                        os.chown(path.parent,spec['native_uid'],pwd.getpwuid(spec['native_uid']).pw_gid)
                    changed.append((name,path))
                    remove_path(path)
                    src=source[name]
                    if src.is_symlink():raise ValueError('账号原配置不能是符号链接')
                    if src.is_dir():safe_copytree(src,path)
                    elif src.is_file():shutil.copy2(src,path)
                    if path.exists():own_tree(path,spec['native_uid'])
                self.manager.history.sync_native(home/'.gemini/antigravity',spec['native_uid'])
            except Exception:
                for name,path in reversed(changed):
                    remove_path(path)
                    if (backup/name).is_dir():safe_copytree(backup/name,path)
                    elif (backup/name).is_file():shutil.copy2(backup/name,path)
                    if path.exists():own_tree(path,spec['native_uid'])
                raise
        self.binding=dict(profile_id=profile_id,native=dict(spec),backup=str(backup))
        atomic_json(self.file,self.binding)
        return True

    def snapshot_to_private(self, profile_id):
        from profile_manager import safe_copytree,remove_path
        spec=self.spec_for(profile_id)
        profile=self.manager.get_profile(profile_id)
        if not spec or profile.get('kind')=='existing':
            return
        home=self.manager.profile_dir(profile_id)/'home'
        home.mkdir(parents=True,exist_ok=True,mode=0o700)
        for name,dest in targets(home).items():
            src=targets(spec['native_home'],spec['native_config_dir'])[name]
            dest.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            remove_path(dest)
            if src.is_dir():safe_copytree(src,dest)
            elif src.is_file():shutil.copy2(src,dest)
        (home/'.suite-home-initialized').write_text('1',encoding='ascii')
        self.manager.history.attach(home)

    def status(self):
        if not self.binding:
            return None
        spec=self.binding['native']
        return dict(profile_id=self.binding['profile_id'],existing_profile_id=spec['id'],
                    home=spec['native_home'],config_dir=spec['native_config_dir'],shared=True)
