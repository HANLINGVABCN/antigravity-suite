"""Loopback HTTP bridge: supports authenticated HTTP(S), SOCKS4 and SOCKS5 nodes."""
import asyncio
import base64
import ssl
from urllib.parse import unquote, urlsplit

from python_socks.async_.asyncio import Proxy


class OutboundProxy:
    def __init__(self, url):
        self.upstream=url
        self.server=None
        self.url=None
        self.connections=set()

    async def __aenter__(self):
        if self.upstream:
            self.server=await asyncio.start_server(self.handle,'127.0.0.1',0,limit=65536)
            self.url='http://127.0.0.1:%d'%self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self,*exc):
        await self.close()

    async def close(self):
        if self.server:
            self.server.close(); await self.server.wait_closed(); self.server=None
        for task in list(self.connections): task.cancel()
        await asyncio.gather(*self.connections,return_exceptions=True)

    async def connect(self,host,port):
        parsed=urlsplit(self.upstream)
        if parsed.scheme=='https':
            reader,writer=await asyncio.wait_for(asyncio.open_connection(parsed.hostname,parsed.port,ssl=ssl.create_default_context()),15)
            authority=('[%s]'%host if ':' in host else host)+':'+str(port)
            headers='CONNECT '+authority+' HTTP/1.1\r\nHost: '+authority+'\r\n'
            if parsed.username is not None:
                auth=unquote(parsed.username)+':'+unquote(parsed.password or '')
                headers+='Proxy-Authorization: Basic '+base64.b64encode(auth.encode()).decode()+'\r\n'
            writer.write((headers+'\r\n').encode()); await writer.drain()
            try:
                status=await asyncio.wait_for(reader.readline(),15)
                if len(status.split())<2 or status.split()[1]!=b'200': raise OSError('HTTPS 代理拒绝连接')
                total=0
                while True:
                    line=await asyncio.wait_for(reader.readline(),15); total+=len(line)
                    if line in (b'\r\n',b''): break
                    if total>65536: raise OSError('代理响应过大')
                return reader,writer
            except BaseException:
                writer.close(); raise
        normalized=self.upstream.replace('socks5h://','socks5://',1).replace('socks4a://','socks4://',1)
        connection=await Proxy.from_url(normalized,rdns=True).connect(host,port,timeout=15)
        try: return await asyncio.open_connection(sock=connection)
        except BaseException: connection.close(); raise

    async def handle(self,reader,writer):
        task=asyncio.current_task(); self.connections.add(task)
        remote=None
        try:
            first=await asyncio.wait_for(reader.readline(),15)
            pieces=first.decode('latin1').strip().split()
            if len(pieces)!=3: raise ValueError('代理请求格式无效')
            method,target,version=pieces
            headers=[]; total=0
            while True:
                line=await asyncio.wait_for(reader.readline(),15); total+=len(line)
                if line in (b'\r\n',b''): break
                if total>65536: raise ValueError('代理请求头过大')
                if line.lower().startswith((b'proxy-authorization:',b'proxy-connection:')): continue
                headers.append(line)
            parsed=urlsplit('http://'+target if method=='CONNECT' else target)
            host=parsed.hostname; port=parsed.port or (443 if method=='CONNECT' else 80)
            if not host or not 1<=port<=65535: raise ValueError('代理目标无效')
            upstream,remote=await self.connect(host,port)
            if method=='CONNECT':
                writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n'); await writer.drain()
            else:
                path=parsed.path or '/'
                if parsed.query:path+='?'+parsed.query
                remote.write(('%s %s %s\r\n'%(method,path,version)).encode('latin1')+b''.join(headers)+b'\r\n')
                await remote.drain()
            async def pipe(src,dst):
                while True:
                    data=await src.read(65536)
                    if not data: break
                    dst.write(data); await dst.drain()
            tasks=[asyncio.create_task(pipe(reader,remote)),asyncio.create_task(pipe(upstream,writer))]
            try: await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
            finally:
                for t in tasks:t.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
        except asyncio.CancelledError: raise
        except Exception:
            try:writer.write(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n');await writer.drain()
            except OSError:pass
        finally:
            if remote:remote.close()
            writer.close()
            self.connections.discard(task)
