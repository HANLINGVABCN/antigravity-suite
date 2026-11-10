"""A single existing runtime shared by desktop and the currently selected account."""
import json
import hashlib
import os
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
        self._login_observation=None
        self.ignore_file=manager.data_dir/'native-login-ignore.json'
        self._ignored_login=json.loads(self.ignore_file.read_text(encoding='utf-8')) if self.ignore_file.is_file() else None
        if self.binding:
            self.validate(self.binding['native'])
            if not manager.get_profile(self.binding['profile_id']):
                raise ValueError('共用运行配置的账号不存在，请恢复私有备份')

    def validate(self, spec):
        data=self.manager.data_dir.resolve()
        if not isinstance(spec.get('native_uid'),int) or isinstance(spec.get('native_uid'),bool) or spec['native_uid']<0:
            raise ValueError('共用配置所属用户无效')
        if spec.get('runtime_kind')=='internal-login':
            home=Path(spec['native_home']).resolve()
            allowed={(data/'login-environment/home').resolve(),self.manager.legacy_home.resolve()}
            if home not in allowed or Path(spec['native_config_dir']).resolve()!=home/'.config/Antigravity':raise ValueError('内部登录运行目录无效')
            return
        for name in ('native_home','native_config_dir'):
            path=Path(spec[name]).resolve()
            if not path.is_absolute() or path==data or path.is_relative_to(data) or len(path.parts)<2:
                raise ValueError('已有共用配置必须位于私有档案目录之外')

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
            self.selected=self.binding['native'] if self.binding['native'].get('runtime_kind')=='internal-login' else next((p for p in available if p['id']==self.binding['native']['id']),None)
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

    def observe_login(self, force=False, native=None):
        native=native or (self.binding['native'] if self.binding else self.selected)
        if not native:return None
        try:self.manager.cli_credentials.ensure(native)
        except (OSError,ValueError):
            # Keep desktop discovery usable when the optional CLI has conflicting credentials.
            pass
        file=Path(native['native_home'])/'.gemini/jetski-standalone-oauth-token'
        token=self.manager.installed_token(file)
        if token is None and file.is_file():
            try:
                value=json.loads(file.read_text(encoding='utf-8'))
                if not isinstance(value,dict) or not isinstance(value.get('token',{}),dict):return None
                if value.get('token',{}).get('refresh_token'):return None
            except (OSError,ValueError):return None
        key=(native['id'],token.get('full_fingerprint') if token else None)
        ignored=self._ignored_login
        if ignored:
            same=token and ignored['native_id']==native['id'] and (ignored['fingerprint']==token['fingerprint'] or
                (token['email'] and ignored.get('email','').lower()==token['email'].lower()))
            if same:return None
            self._ignored_login=None;self.ignore_file.unlink(missing_ok=True)
        if not force and self._login_observation!=key:
            self._login_observation=key;return None
        self._login_observation=key
        return dict(native=native,token=token,state='logged_in' if token else 'logged_out')

    def associate(self, profile_id, native):
        self.selected=dict(native)
        self.binding=dict(profile_id=profile_id,native=dict(native))
        atomic_json(self.file,self.binding)

    def detach(self):
        if self.binding and self.binding['native'].get('runtime_kind')=='internal-login':self.selected=self.binding['native']
        self.binding=None
        self.file.unlink(missing_ok=True)

    def clear_ignored_login(self):
        self._ignored_login=None;self.ignore_file.unlink(missing_ok=True)

    def ignore_current_login(self):
        native=self.binding['native'] if self.binding else self.selected
        if not native:return
        token=self.manager.installed_token(Path(native['native_home'])/'.gemini/jetski-standalone-oauth-token')
        if token:
            self._ignored_login=dict(native_id=native['id'],**token)
            atomic_json(self.ignore_file,self._ignored_login)

    def activate(self, profile_id):
        from account_storage import archive_bytes,working_file,read_bytes,write_bytes
        profile=self.manager.get_profile(profile_id)
        spec=profile if profile and profile.get('kind')=='existing' else self.selected or (self.binding or {}).get('native')
        if not spec:return False
        self.validate(spec)
        if not spec.get('available'):raise ValueError('原运行环境不可用，请检查本体和目录权限')
        if self.binding and self.binding['profile_id']==profile_id and self.binding['native']['id']==spec['id']:
            self.manager.cli_credentials.ensure(spec,prefer_native=True);return True
        self.manager.cli_credentials.stop(spec)
        self.manager.cli_credentials.ensure(spec,prefer_native=True)
        target=working_file(spec['native_home']);previous=read_bytes(target)
        old_binding=self.binding
        try:
            if profile.get('kind')!='existing':write_bytes(target,archive_bytes(self.manager,profile_id),spec['native_uid'])
            self.associate(profile_id,spec)
        except Exception:
            write_bytes(target,previous,spec['native_uid']);self.binding=old_binding
            if old_binding:atomic_json(self.file,old_binding)
            else:self.file.unlink(missing_ok=True)
            raise
        return True

    def snapshot_to_private(self, profile_id):
        # Legacy entry point: archives only the current credential, never a HOME.
        return self.manager.save_active_to_profile(profile_id)

    def status(self):
        if not self.binding:
            return None
        spec=self.binding['native']
        return dict(profile_id=self.binding['profile_id'],existing_profile_id=spec['id'],
                    home=spec['native_home'],config_dir=spec['native_config_dir'],shared=True)
