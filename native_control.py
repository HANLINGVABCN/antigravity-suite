"""Limited local controls for the native desktop; no administrator credentials are shared."""
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

from config import atomic_json
from native_runtime import own_tree


class NativeControl:
    def __init__(self,state):
        self.state=state;self.secret=secrets.token_bytes(32);self.contexts={}

    def token(self,identifier):return hmac.new(self.secret,identifier.encode(),hashlib.sha256).hexdigest()

    def publish(self,spec):
        home=Path(spec['native_home']).resolve()
        identifier=spec.get('id') or hashlib.sha256((str(home)+'\0'+spec['native_config_dir']).encode()).hexdigest()[:32]
        spec=dict(spec,id=identifier)
        folder=home/'.cache/antigravity-suite'
        if not folder.resolve().is_relative_to(home):raise ValueError('原生面板目录不能指向运行目录以外')
        cache=home/'.cache';created=not cache.exists()
        folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        host=self.state.cfg.get('listen_host','127.0.0.1')
        host='127.0.0.1' if host in ('0.0.0.0','127.0.0.1') else '::1' if host in ('::','::1') else host
        if host not in ('127.0.0.1','::1','localhost'):raise ValueError('原生面板需要管理服务提供本机监听地址')
        url='http://%s:%s'%('['+host+']' if ':' in host else host,self.state.cfg['listen_port'])
        file=folder/'native-control.json'
        value=dict(runtime_id=identifier,token=self.token(identifier),url=url)
        try:unchanged=json.loads(file.read_text(encoding='utf-8'))==value and not file.is_symlink()
        except (OSError,ValueError):unchanged=False
        if not unchanged:atomic_json(file,value)
        uid=spec['native_uid']
        if hasattr(os,'chown'):
            import pwd
            gid=pwd.getpwuid(uid).pw_gid
            if created:os.chown(cache,uid,gid)
            os.chown(folder,uid,gid);os.chown(file,uid,gid)
        self.contexts[identifier]=spec
        return spec

    def authorize(self,request,data):
        if request.remote not in ('127.0.0.1','::1'):return None
        identifier=data.get('runtime_id')
        if identifier not in self.contexts:return None
        supplied=request.headers.get('X-Antigravity-Native-Control','')
        if not hmac.compare_digest(supplied,self.token(identifier)):return None
        return self.contexts[identifier]

    def minimal_status(self):
        s=self.state;status=s.status()
        result={key:status.get(key) for key in ('active_profile','active_profile_id','profiles','active_proxy','antigravity_ready',
            'starting_profile_id','default_profile_id','native_session','exit_state','login_environment_ready','login_sessions')}
        keep={'id','name','email','proxy_id','created_at','last_used_at'}
        result['profiles']=[{k:v for k,v in p.items() if k in keep} for p in result['profiles']]
        if result['active_profile']:result['active_profile']={k:v for k,v in result['active_profile'].items() if k in keep}
        if result['active_proxy']:result['active_proxy']={k:result['active_proxy'][k] for k in ('id','name')}
        return result
