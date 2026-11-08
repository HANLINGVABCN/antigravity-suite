"""Bounded in-memory cache for guarded native JS; authenticated private revalidation."""
import asyncio
import gzip
import hashlib

from aiohttp import web

from logout_protection import transform_client


def accepts_gzip(value):
    encodings={}
    for part in value.lower().split(','):
        fields=part.strip().split(';');name=fields[0].strip()
        quality=1.0
        for field in fields[1:]:
            if field.strip().startswith('q='):
                try:quality=float(field.strip()[2:])
                except ValueError:quality=0
        encodings[name]=quality
    return encodings.get('gzip',encodings.get('*',0))>0


def matches_etag(value,etag):
    return any(tag.strip()=='*' or tag.strip().removeprefix('W/')==etag for tag in value.split(','))


def build_script(raw,bootstrap):
    source,supported=transform_client(raw.decode('utf-8'),bootstrap)
    identity=source.encode('utf-8')
    compressed=gzip.compress(identity,compresslevel=6,mtime=0)
    return dict(identity=identity,gzip=compressed,supported=supported,
        etag_identity='"agy-'+hashlib.sha256(identity).hexdigest()+'"',
        etag_gzip='"agy-'+hashlib.sha256(compressed).hexdigest()+'-gzip"')


class ClientAssets:
    def __init__(self):
        self.lock=asyncio.Lock();self.key=None;self.entry=None

    async def guarded_script(self,raw,bootstrap,context):
        # Including runtime/profile and exact source bytes prevents reuse after native restart,
        # account switching, vendor update or guard update. Only the latest entry is kept.
        async with self.lock:
            def fingerprint():
                return (context,hashlib.sha256(raw).digest(),hashlib.sha256(bootstrap.encode('utf-8')).digest())
            key=await asyncio.to_thread(fingerprint)
            if key!=self.key:
                entry=await asyncio.to_thread(build_script,raw,bootstrap)
                self.key=key;self.entry=entry
            return self.entry


def script_response(request,entry,headers):
    out=headers.copy()
    for name in ('Content-Length','Content-Encoding','ETag','Content-MD5','Last-Modified','Expires','Accept-Ranges','Content-Range','Set-Cookie'):
        out.popall(name,None)
    # No shared/proxy cache; every revalidation still passes the admin authentication.
    out['Cache-Control']='private, no-cache, must-revalidate'
    vary=[v.strip() for v in out.get('Vary','').split(',') if v.strip()]
    if 'accept-encoding' not in [v.lower() for v in vary]:vary.append('Accept-Encoding')
    out['Vary']=', '.join(vary)
    out['Content-Type']='application/javascript; charset=utf-8'
    compressed=accepts_gzip(request.headers.get('Accept-Encoding',''))
    etag=entry['etag_gzip'] if compressed else entry['etag_identity']
    out['ETag']=etag
    if matches_etag(request.headers.get('If-None-Match',''),etag):return web.Response(status=304,headers=out)
    if compressed:out['Content-Encoding']='gzip'
    return web.Response(body=entry['gzip'] if compressed else entry['identity'],headers=out)
