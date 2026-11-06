"""A shared local conversation library, separate from every account's credentials."""
import hashlib
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

DIRECTORIES=('conversations','brain')
DATABASE='conversation_summaries.db'


class SharedHistory:
    def __init__(self,data_dir):
        self.root=Path(data_dir)/'shared-history'
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        for name in DIRECTORIES:(self.root/name).mkdir(exist_ok=True,mode=0o700)

    def merge_tree(self,source,dest):
        source,dest=Path(source),Path(dest)
        if not source.is_dir() or source.is_symlink():return
        for item in source.rglob('*'):
            if item.is_symlink():continue
            rel=item.relative_to(source);target=dest/rel
            if item.is_dir():target.mkdir(parents=True,exist_ok=True)
            elif item.is_file():
                target.parent.mkdir(parents=True,exist_ok=True)
                if target.exists():
                    if hashlib.sha256(item.read_bytes()).digest()==hashlib.sha256(target.read_bytes()).digest():continue
                    # Preserve both versions when duplicate IDs differ; newest is active.
                    conflict=self.root/'conflicts'/str(time.time_ns())/rel
                    conflict.parent.mkdir(parents=True,exist_ok=True)
                    older,newer=(target,item) if item.stat().st_mtime>=target.stat().st_mtime else (item,target)
                    shutil.copy2(older,conflict)
                    if newer==target:continue
                shutil.copy2(item,target)

    def merge_database(self,source):
        source=Path(source);target=self.root/DATABASE
        if not source.is_file() or source.is_symlink():return
        # Snapshot through SQLite so committed WAL data is included.
        with tempfile.TemporaryDirectory(dir=self.root,prefix='.db-import-') as tmp:
            snapshot=Path(tmp)/'source.db'
            src=sqlite3.connect('file:'+source.as_posix()+'?mode=ro',uri=True)
            backup=sqlite3.connect(snapshot)
            try:src.backup(backup)
            finally:backup.close();src.close()
            if not target.exists():
                shutil.copy2(snapshot,target);os.chmod(target,0o600);return
            incoming=sqlite3.connect(snapshot);incoming.row_factory=sqlite3.Row
            outgoing=sqlite3.connect(target)
            try:
                exists=incoming.execute("SELECT name FROM sqlite_master WHERE name='conversation_summaries'").fetchone()
                if not exists:return
                target_columns={row[1] for row in outgoing.execute('PRAGMA table_info(conversation_summaries)')}
                columns=[row[1] for row in incoming.execute('PRAGMA table_info(conversation_summaries)') if row[1] in target_columns]
                if 'conversation_id' not in columns:raise ValueError('无法合并对话索引，请保留备份并检查版本')
                fields=','.join('"'+c.replace('"','""')+'"' for c in columns)
                query='INSERT OR REPLACE INTO conversation_summaries ('+fields+') VALUES ('+','.join('?' for _ in columns)+')'
                for row in incoming.execute('SELECT '+fields+' FROM conversation_summaries'):
                    existing=outgoing.execute('SELECT last_modified_time FROM conversation_summaries WHERE conversation_id=?',(row['conversation_id'],)).fetchone()
                    if existing and 'last_modified_time' in columns and str(existing[0])>=str(row['last_modified_time']):continue
                    outgoing.execute(query,tuple(row[c] for c in columns))
                # Merge the search index through its public FTS table, never shadow tables.
                fts=incoming.execute("SELECT name FROM sqlite_master WHERE name='conversations_fts'").fetchone()
                target_fts=outgoing.execute("SELECT name FROM sqlite_master WHERE name='conversations_fts'").fetchone()
                if fts and target_fts:
                    src_columns=[r[1] for r in incoming.execute('PRAGMA table_info(conversations_fts)')]
                    dst_columns=[r[1] for r in outgoing.execute('PRAGMA table_info(conversations_fts)')]
                    if src_columns==dst_columns and 'conversation_id' in src_columns:
                        names=','.join('"'+c+'"' for c in src_columns)
                        for (cid,) in incoming.execute('SELECT DISTINCT conversation_id FROM conversations_fts'):
                            if outgoing.execute('SELECT 1 FROM conversations_fts WHERE conversation_id=? LIMIT 1',(cid,)).fetchone():continue
                            rows=incoming.execute('SELECT '+names+' FROM conversations_fts WHERE conversation_id=?',(cid,))
                            outgoing.executemany('INSERT INTO conversations_fts ('+names+') VALUES ('+','.join('?' for _ in src_columns)+')',[tuple(r) for r in rows])
                outgoing.commit()
            finally:incoming.close();outgoing.close()

    def merge_native(self,native):
        native=Path(native)
        for name in DIRECTORIES:self.merge_tree(native/name,self.root/name)
        self.merge_database(native/DATABASE)

    def attach(self,home):
        native=Path(home)/'.gemini/antigravity'
        native.mkdir(parents=True,exist_ok=True,mode=0o700)
        for name in DIRECTORIES:
            path=native/name;target=self.root/name
            if path.is_symlink():
                if path.resolve()!=target.resolve():raise ValueError('对话库链接指向其他目录')
                continue
            if path.exists():
                self.merge_tree(path,target)
                backup=self.root/'migration-backups'/str(time.time_ns())/name
                backup.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(path),str(backup))
            path.symlink_to(target.resolve(),target_is_directory=True)
        db=native/DATABASE
        if not db.is_symlink():
            if db.exists():
                self.merge_database(db)
                backup=self.root/'migration-backups'/str(time.time_ns());backup.mkdir(parents=True)
                for suffix in ('','-wal','-shm','-journal'):
                    file=native/(DATABASE+suffix)
                    if file.exists() and not file.is_symlink():shutil.move(str(file),str(backup/file.name))
            db.symlink_to((self.root/DATABASE).resolve())
        elif db.resolve()!=(self.root/DATABASE).resolve():raise ValueError('对话索引链接指向其他目录')

    def summary(self):
        return dict(shared=True,conversations=sum(1 for p in (self.root/'conversations').glob('*') if p.is_file()),
                    brain_sessions=sum(1 for p in (self.root/'brain').iterdir() if p.is_dir()))
    def backup_database(self, target):
        source=self.root/DATABASE
        if not source.is_file():return False
        src=sqlite3.connect('file:'+source.as_posix()+'?mode=ro',uri=True)
        dest=sqlite3.connect(target)
        try:src.backup(dest)
        finally:dest.close();src.close()
        return True
