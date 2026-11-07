"""Track explicit login generations independently of account names and token renewal."""
import secrets
import time
from pathlib import Path
import json
import re

from config import atomic_json


class LoginSessions:
    def __init__(self, data_dir):
        self.file=Path(data_dir)/'login-session-state.json'
        self.state=json.loads(self.file.read_text(encoding='utf-8')) if self.file.is_file() else {}
        self.state.setdefault('waiting',False)
        self.state.setdefault('pending',None)
        self.state.setdefault('completed',{})

    def save(self):atomic_json(self.file,self.state)

    def begin(self, source, token=None):
        if source not in ('native','web','panel'):raise ValueError('登录来源无效')
        nonce=secrets.token_hex(16)
        self.state['pending']=dict(id=nonce,source=source,started_at=time.time(),confirmed=False,valid_auth=False,
            baseline=token.get('full_fingerprint') if token else None)
        self.save()
        return nonce

    def complete(self, nonce, valid_auth=False):
        if not isinstance(nonce,str) or not re.fullmatch('[a-f0-9]{32}',nonce):raise ValueError('登录操作编号无效')
        if nonce in self.state['completed']:return self.state['completed'][nonce]
        pending=self.state.get('pending')
        if not pending or pending['id']!=nonce:raise ValueError('登录操作已失效，请重新开始登录')
        if valid_auth is not True:
            self.state['pending']=None;self.save();return None
        pending.update(confirmed=True,valid_auth=True,completed_at=time.time())
        self.save()
        return None

    def restored(self, profile_id, token=None):
        self.state.update(profile_id=profile_id,waiting=False,pending=None,
            credential=token.get('fingerprint') if token else None,
            full=token.get('full_fingerprint') if token else None)
        self.save()

    def exited(self, working=None):
        self.state.update(profile_id=None,credential=None,full=None,waiting=True,pending=None,working=working)
        self.save()

    def new_login(self, token, active_id, matches_active):
        if not token:return None
        pending=self.state.get('pending')
        if pending and time.time()-pending['started_at']>1800:
            self.state['pending']=None;self.save();pending=None
        explicit=bool(pending and pending.get('confirmed') and pending.get('valid_auth'))
        if pending and not explicit:return None
        if explicit or self.state['waiting'] or not active_id or not matches_active:
            if not pending:
                nonce=self.begin('native',None)
                pending=self.state['pending'];pending['confirmed']=True;self.save()
            return pending['id']
        return None

    def captured(self, nonce, profile_id, token):
        completed=self.state['completed']
        completed[nonce]=profile_id
        while len(completed)>50:del completed[next(iter(completed))]
        self.state.update(profile_id=profile_id,pending=None,waiting=False,credential=token['fingerprint'],
            full=token.get('full_fingerprint'))
        self.save()

    def renewed(self, profile_id, token):
        if self.state.get('profile_id')==profile_id and self.state.get('full')==token.get('full_fingerprint'):return
        self.state.update(profile_id=profile_id,credential=token['fingerprint'],full=token.get('full_fingerprint'))
        self.save()

    def status(self):
        return dict(waiting_for_login=self.state['waiting'],login_in_progress=self.in_progress())

    def in_progress(self):
        pending=self.state.get('pending')
        return bool(pending and not pending.get('confirmed') and time.time()-pending['started_at']<=1800)
