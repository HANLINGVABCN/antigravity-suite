"""Download official GitHub releases and hand off to an isolated update worker."""
import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import aiohttp
from config import SUITE_DIR, atomic_json
from service_control import detect_controller, launch_worker


REPOSITORY='HANLINGVABCN/antigravity-suite'
RELEASE_API='https://api.github.com/repos/'+REPOSITORY+'/releases/latest'
BUSY_PHASES={'queued','downloading','verifying','dependencies','backup','installing','restarting','rolling_back'}
PRIVATE={'data','profiles','runtime-home','shared-history','backups','core','.venv','.git','updates','__pycache__'}
PRIVATE_FILES={'config.json','profiles.json','proxies.json','initial-credentials.txt','.env','shared-native-runtime.json'}


def version_tuple(value):
    match=re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)',str(value))
    if not match:raise ValueError('发布版本格式无效')
    return tuple(map(int,match.groups()))


def safe_source_name(name):
    parts=PurePosixPath(name).parts
    if not parts or name.startswith('/') or '\\' in name or any(part in ('','..','.') for part in parts):
        raise ValueError('更新包包含不安全路径')
    if PRIVATE.intersection(parts) or any(part in PRIVATE_FILES for part in parts):
        raise ValueError('更新包不能包含运行配置、账号凭据或本体')
    if parts[0] not in {'.github','static','docs','examples','tests','tools','systemd'} and len(parts)>1:
        raise ValueError('更新包包含未知源码目录')
    if PurePosixPath(name).suffix.lower() not in {'.py','.js','.css','.html','.md','.txt','.sh','.ini','.yml','.yaml','.toml','.conf','.service',''}:
        raise ValueError('更新包包含不支持的源码文件')
    return name


def extract_release(archive,source,version):
    source=Path(source);source.mkdir(parents=True,exist_ok=True,mode=0o700)
    files={};total=0
    with zipfile.ZipFile(archive) as bundle:
        if len(bundle.infolist())>2000:raise ValueError('更新包文件数量超出限制')
        for item in bundle.infolist():
            if item.is_dir():continue
            if not item.filename.startswith('antigravity-suite/'):
                raise ValueError('不是本项目的更新包')
            name=safe_source_name(item.filename[len('antigravity-suite/'):])
            if name in files or item.external_attr>>16 & 0o170000==0o120000:
                raise ValueError('更新包包含重复文件或符号链接')
            total+=item.file_size
            if item.file_size>4*1024*1024 or total>64*1024*1024:raise ValueError('更新包大小超出限制')
            target=source/name;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            content=bundle.read(item);target.write_bytes(content);os.chmod(target,0o600)
            files[name]=hashlib.sha256(content).hexdigest()
    if any(name not in files for name in ('VERSION','server.py','config.py','requirements.txt','static/portal.html','update_worker.py','service_control.py')):
        raise ValueError('更新包缺少必要文件')
    if (source/'VERSION').read_text().strip()!=version:raise ValueError('更新包版本与发布信息不一致')
    return files


def release_asset(release,name):
    tag=release['tag_name']
    asset=next((a for a in release.get('assets',[]) if a.get('name')==name and a.get('state','uploaded')=='uploaded'),None)
    if not asset:raise ValueError('此版本缺少可校验的官方源码更新包')
    url=asset.get('browser_download_url','')
    expected='https://github.com/'+REPOSITORY+'/releases/download/'+tag+'/'+name
    if url!=expected or not isinstance(asset.get('size'),int) or not 0<asset['size']<=32*1024*1024:
        raise ValueError('发布附件来源或大小无效')
    return asset


class SuiteUpdater:
    def __init__(self,state):
        self.state=state;self.root=state.data/'updates';self.status_file=self.root/'status.json'
        self.task=None;self.check_lock=asyncio.Lock();self.start_lock=asyncio.Lock();self.release=None
        self.checked_at=0;self.last_check_error=''
        self.controller=detect_controller()
        old=self.job_status()
        if old.get('phase') in BUSY_PHASES:
            try:
                command=(Path('/proc')/str(old['worker_pid'])/'cmdline').read_bytes()
                alive=old['job_id'].encode() in command
            except (OSError,KeyError):alive=False
            if not alive:
                atomic_json(self.status_file,dict(old,phase='error',message='可能是上次更新进程意外退出，请检查服务状态后重试'))
                (self.root/'update.lock').unlink(missing_ok=True)

    def current_version(self):return (SUITE_DIR/'VERSION').read_text().strip()

    def job_status(self):
        try:return json.loads(self.status_file.read_text(encoding='utf-8'))
        except (OSError,ValueError):return dict(phase='idle',message='尚未执行更新')

    def busy(self):return self.job_status().get('phase') in BUSY_PHASES

    def status(self):
        latest=self.release['tag_name'][1:] if self.release else None
        current=self.current_version()
        return dict(current_version=current,latest_version=latest,
            update_available=bool(latest and version_tuple(latest)>version_tuple(current)),
            supported=bool(self.controller and os.access(SUITE_DIR,os.W_OK)),
            support_reason='' if self.controller else '此部署未识别到可控制的 systemd 或 Supervisor 服务，请使用部署说明升级',
            checked_at=self.checked_at,check_error=self.last_check_error,release_url=self.release.get('html_url') if self.release else None,
            notes=(self.release.get('body') or '')[:16000] if self.release else '',job=self.job_status())

    async def _download(self,url,dest,limit,expected_size=None):
        timeout=aiohttp.ClientTimeout(total=180,sock_connect=15)
        async with aiohttp.ClientSession(timeout=timeout,trust_env=False,cookie_jar=aiohttp.DummyCookieJar()) as client:
            async with client.get(url,headers={'User-Agent':'antigravity-suite-updater','Accept':'application/vnd.github+json'},allow_redirects=True) as response:
                if response.status!=200:raise ValueError('发布服务器暂时不可用，请稍后重试')
                final=urlsplit(str(response.url))
                if final.scheme!='https' or final.hostname not in {'api.github.com','github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'}:
                    raise ValueError('更新附件跳转来源无效')
                size=0
                with Path(dest).open('wb') as output:
                    async for content in response.content.iter_chunked(65536):
                        size+=len(content)
                        if size>limit:raise ValueError('发布附件超过大小限制')
                        output.write(content)
                if expected_size is not None and size!=expected_size:raise ValueError('发布附件下载不完整')

    async def check(self):
        async with self.check_lock:
            self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
            path=self.root/'release-check.json'
            try:
                await self._download(RELEASE_API,path,1024*1024)
                release=json.loads(path.read_text(encoding='utf-8'))
                version_tuple(release.get('tag_name'))
                if release.get('draft') or release.get('prerelease') or not release['tag_name'].startswith('v'):
                    raise ValueError('只支持正式发布版本')
                if release.get('html_url')!='https://github.com/'+REPOSITORY+'/releases/tag/'+release['tag_name']:
                    raise ValueError('发布信息来源无效')
                name='antigravity-suite-'+release['tag_name']+'-source.zip'
                release_asset(release,name);release_asset(release,name+'.sha256')
                self.release=release;self.checked_at=time.time();self.last_check_error=''
            except Exception:
                self.release=None;self.last_check_error='可能是 GitHub 暂时不可用、请求限速或网络连接失败，请稍后重试'
                raise ValueError(self.last_check_error)
            finally:path.unlink(missing_ok=True)
        return self.status()

    async def start(self):
        async with self.start_lock:
            if self.busy():return self.status()
            if not self.controller:raise ValueError(self.status()['support_reason'])
            await self.check()
            if not self.status()['update_available']:return self.status()
            if not os.access(SUITE_DIR,os.W_OK):raise ValueError('管理服务没有源码目录写权限，无法自动更新')
            if shutil.disk_usage(self.state.data).free<768*1024*1024:raise ValueError('可用磁盘空间不足，请至少保留 768MB 后再更新')
            self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
            job_id=secrets.token_hex(12);lock=self.root/'update.lock'
            try:
                fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            except FileExistsError:raise ValueError('已有更新任务，请等待它结束后重试')
            with os.fdopen(fd,'w') as output:output.write(job_id)
            job=self.root/job_id;job.mkdir(mode=0o700)
            plan=dict(job_id=job_id,job_dir=str(job),live=str(SUITE_DIR),data=str(self.state.data),
                source=str(job/'source'),state_file=str(self.status_file),controller=self.controller,
                version=self.release['tag_name'][1:],started_at=time.time())
            atomic_json(self.status_file,dict(job_id=job_id,phase='queued',message='更新任务已提交',target_version=plan['version'],started_at=plan['started_at']))
            self.task=asyncio.create_task(self.prepare(plan,dict(self.release)))
            return self.status()

    async def prepare(self,plan,release):
        from update_worker import progress
        job=Path(plan['job_dir']);lock=self.root/'update.lock'
        try:
            progress(plan,'downloading','正在从项目 GitHub 发布页下载更新包')
            name='antigravity-suite-'+release['tag_name']+'-source.zip'
            archive=release_asset(release,name);checksum=release_asset(release,name+'.sha256')
            await self._download(archive['browser_download_url'],job/'source.zip',32*1024*1024,archive['size'])
            await self._download(checksum['browser_download_url'],job/'source.zip.sha256',4096,checksum['size'])
            progress(plan,'verifying','正在校验更新包及版本')
            text=(job/'source.zip.sha256').read_text().strip()
            if not re.fullmatch(r'[a-fA-F0-9]{64}\s+\*?'+re.escape(name),text):raise ValueError('发布校验文件格式无效')
            expected=text.split()[0].lower();actual=hashlib.sha256((job/'source.zip').read_bytes()).hexdigest()
            if actual!=expected:raise ValueError('更新包 SHA256 不匹配')
            digest=archive.get('digest')
            if digest and digest!='sha256:'+actual:raise ValueError('GitHub 发布附件校验值不匹配')
            plan['files']=await asyncio.to_thread(extract_release,job/'source.zip',Path(plan['source']),plan['version'])
            for filename in ('update_worker.py','service_control.py'):
                shutil.copy2(SUITE_DIR/filename,job/filename)
            atomic_json(job/'plan.json',plan)
            await asyncio.to_thread(launch_worker,self.controller,sys.executable,job/'update_worker.py',job/'plan.json',plan['job_id'],job/'worker.log')
        except Exception:
            progress(plan,'error','可能是网络连接、下载校验、源码格式或更新进程启动失败；原服务未被修改')
            if lock.is_file() and lock.read_text().strip()==plan['job_id']:lock.unlink()
