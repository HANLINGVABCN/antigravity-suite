import asyncio
import hashlib
import base64
import os
import platform
import tempfile
import shutil
from pathlib import Path
from urllib.parse import urlsplit


class CoreInstaller:
    def __init__(self, cfg, supervisor):
        self.cfg, self.supervisor = cfg, supervisor
        self.state = {'status': 'idle', 'message': '', 'log': ''}
        self.task = None

    def start(self):
        if self.task and not self.task.done():
            return
        if self.supervisor.process and self.supervisor.process.poll() is None:
            raise ValueError('请先停止正在运行的 IDE 再安装本体')
        self.state = {'status': 'queued', 'message': '已提交安装任务', 'log': ''}
        self.task = asyncio.create_task(self.install())
    def start_uninstall(self):
        if self.task and not self.task.done(): raise ValueError('已有本体操作正在进行')
        self.state = {'status':'uninstalling','message':'正在卸载本套件的反重力本体','log':''}
        self.task = asyncio.create_task(self.uninstall())
    def managed_directory(self):
        root=Path(self.cfg.get('core_directory',Path(self.cfg.get('data_dir',self.cfg['runtime_home'])).parent/'core'))
        return root.resolve()
    async def uninstall(self):
        try:
            root=self.managed_directory()
            binary=Path(self.supervisor.antigravity_bin).resolve()
            if root==Path('/') or root in binary.parents and len(root.parts)<3: raise ValueError('本体目录配置无效')
            if root not in binary.parents: raise ValueError('当前本体由服务器其他应用共享，不能从本套件卸载')
            if root.exists(): await asyncio.to_thread(shutil.rmtree,root)
            self.state.update(status='done',message='本套件的反重力本体已卸载；账号档案和备份保留')
        except Exception as e: self.state.update(status='error',message=str(e))

    async def run(self, args):
        env = dict(os.environ, DEBIAN_FRONTEND='noninteractive')
        proc = await asyncio.create_subprocess_exec(*args, env=env, stdout=asyncio.subprocess.PIPE,
                                                   stderr=asyncio.subprocess.STDOUT)
        async def read():
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk: break
                self.state['log'] = (self.state['log'] + chunk.decode(errors='replace'))[-16000:]
            return await proc.wait()
        try:
            code = await asyncio.wait_for(read(), 900)
            if code: raise RuntimeError('命令执行失败，请查看安装日志（退出码 %d）' % code)
        except BaseException:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise

    async def install(self):
        try:
            if platform.system() != 'Linux': raise RuntimeError('网页安装需要 Linux 环境')
            url = self.cfg['core_deb_url']
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or parsed.hostname != 'storage.googleapis.com':
                raise ValueError('安装包必须来自 Google 官方 HTTPS 下载地址')
            with tempfile.TemporaryDirectory(prefix='antigravity-install-') as tmp:
                deb = Path(tmp) / 'Antigravity.deb'
                self.state.update(status='downloading', message='正在下载官方安装包')
                await self.run(['curl', '--fail', '--location', '--proto', '=https', '--proto-redir', '=https',
                                '--connect-timeout', '20', '--max-time', '600', '--output', str(deb), url])
                expected = self.cfg.get('core_deb_sha256')
                if expected:
                    digest = hashlib.sha256()
                    with deb.open('rb') as f:
                        for chunk in iter(lambda: f.read(1024 * 1024), b''): digest.update(chunk)
                    if digest.hexdigest().lower() != expected.lower(): raise RuntimeError('安装包 SHA256 不匹配')
                expected512=self.cfg.get('core_deb_sha512')
                if expected512:
                    digest=hashlib.sha512()
                    with deb.open('rb') as f:
                        for chunk in iter(lambda:f.read(1024*1024),b''):digest.update(chunk)
                    if base64.b64encode(digest.digest()).decode()!=expected512:raise RuntimeError('安装包 SHA512 不匹配')
                self.state.update(status='installing', message='正在解包独立本体')
                extracted=Path(tmp)/'extracted'
                await self.run(['dpkg-deb','--extract',str(deb),str(extracted)])
                candidates=[p for p in extracted.rglob('antigravity') if p.is_file() and not p.is_symlink()
                            and os.access(p,os.X_OK) and (p.parent/'resources/app.asar').is_file()]
                if len(candidates)!=1: raise ValueError('无法识别安装包中的反重力本体，请检查版本与架构')
                root=self.managed_directory()
                root.parent.mkdir(parents=True,exist_ok=True)
                previous=root.with_name(root.name+'.previous')
                if previous.exists(): await asyncio.to_thread(shutil.rmtree,previous)
                if root.exists(): os.replace(root,previous)
                try: await asyncio.to_thread(shutil.copytree,candidates[0].parent,root)
                except Exception:
                    if root.exists(): await asyncio.to_thread(shutil.rmtree,root)
                    if previous.exists(): os.replace(previous,root)
                    raise
                if previous.exists(): await asyncio.to_thread(shutil.rmtree,previous)
                self.cfg['antigravity_bin']=str(root/'antigravity')
                self.supervisor.antigravity_bin=self.cfg['antigravity_bin']
                from config import atomic_json
                atomic_json(Path(self.cfg.get('data_dir',Path(self.cfg['runtime_home']).parent))/'config.json',self.cfg)
                if not self.supervisor.installed(): raise RuntimeError('安装命令结束，但未找到可执行的反重力本体')
                self.state.update(status='done', message='本体安装完成，可以启动档案')
        except asyncio.CancelledError:
            self.state.update(status='error', message='安装任务被取消')
            raise
        except Exception as e:
            self.state.update(status='error', message=str(e))
