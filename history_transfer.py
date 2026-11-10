"""Explicit merge/replace with temporary rollback, never a routine history backup."""
import os
import json
import shutil
import tempfile
from pathlib import Path


class HistoryTransfer:
    def __init__(self,history,sources,mode):
        from shared_history import SharedHistory,DATABASE,DIRECTORIES
        if mode not in ('merge','replace'):raise ValueError('聊天导入方式必须为合并或覆盖')
        self.history=history;self.temporary=tempfile.TemporaryDirectory(dir=history.root.parent,prefix='.history-transfer-')
        self.folder=Path(self.temporary.name);self.old=self.folder/'old';self.old.mkdir(mode=0o700)
        self.builder=SharedHistory(self.folder/'new');self.changed=[];self.committed=False
        self.journal=self.folder/'journal.json';self.planned=[]
        self.entries=(*DIRECTORIES,DATABASE,DATABASE+'-wal',DATABASE+'-shm',DATABASE+'-journal','conflicts')
        self.uid=history.root.stat().st_uid
        try:
            if mode=='merge':self.builder.merge_native(history.root)
            for source in sources:self.builder.merge_native(source)
            if mode=='merge' and (history.root/'conflicts').is_dir():
                self.builder.merge_tree(history.root/'conflicts',self.builder.root/'conflicts')
        except Exception:
            self.temporary.cleanup();raise

    def apply(self):
        from native_runtime import own_tree
        try:
            for name in self.entries:
                target=self.history.root/name;incoming=self.builder.root/name
                if target.is_symlink():raise ValueError('覆盖导入不能替换聊天库以外的链接')
                existed=target.exists()
                self.planned.append(dict(name=name,existed=existed));self.record('applying')
                if existed:os.replace(target,self.old/name)
                self.changed.append((name,existed))
                if incoming.exists():
                    os.replace(incoming,target)
                    if hasattr(os,'geteuid') and os.geteuid()==0:own_tree(target,self.uid)
            return self
        except Exception:
            self.rollback();raise

    def rollback(self):
        while self.changed:
            name,existed=self.changed[-1]
            target=self.history.root/name
            if target.is_dir():shutil.rmtree(target)
            else:target.unlink(missing_ok=True)
            if existed and (self.old/name).exists():os.replace(self.old/name,target)
            self.changed.pop()

    def record(self,phase):
        from config import atomic_json
        atomic_json(self.journal,dict(version=1,root=str(self.history.root.resolve()),phase=phase,entries=self.planned))

    def commit(self):
        self.record('committed');self.committed=True

    def close(self):
        if not self.committed:
            try:self.rollback()
            except Exception:
                self.temporary._finalizer.detach()
                raise
        self.temporary.cleanup()


def recover(history):
    """Restore a transfer interrupted before commit, using its bounded journal."""
    from shared_history import HISTORY_ENTRIES
    allowed=HISTORY_ENTRIES|{'conflicts'}
    for folder in history.root.parent.glob('.history-transfer-*'):
        journal=folder/'journal.json'
        if folder.is_symlink() or not journal.is_file() or journal.is_symlink():continue
        value=json.loads(journal.read_text())
        if value.get('version')!=1 or value.get('root')!=str(history.root.resolve()):continue
        entries=value.get('entries')
        if (not isinstance(entries,list) or len(entries)>len(allowed)
                or any(not isinstance(e,dict) or e.get('name') not in allowed or not isinstance(e.get('existed'),bool) for e in entries)
                or len({e['name'] for e in entries})!=len(entries)
                or value.get('phase') not in ('applying','committed')):raise ValueError('聊天导入恢复记录格式异常')
        if value.get('phase')!='committed':
            for entry in reversed(value['entries']):
                target=history.root/entry['name'];old=folder/'old'/entry['name']
                if old.is_symlink() or target.is_symlink():raise ValueError('聊天导入恢复路径被修改')
                if old.exists() or not entry['existed']:
                    if target.is_dir():shutil.rmtree(target)
                    else:target.unlink(missing_ok=True)
                    if old.exists():os.replace(old,target)
        shutil.rmtree(folder)
