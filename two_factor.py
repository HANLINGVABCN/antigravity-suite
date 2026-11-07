"""Authenticator-app TOTP (RFC 6238) and single-use recovery codes."""
import base64
import hashlib
import hmac
import re
import secrets
import struct
import time
from urllib.parse import quote, urlencode


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode('ascii').rstrip('=')


def totp(secret, counter, digits=6):
    raw=base64.b32decode(secret.upper()+'='*((8-len(secret)%8)%8),casefold=True)
    digest=hmac.new(raw,struct.pack('>Q',counter),hashlib.sha1).digest()
    offset=digest[-1]&15
    number=struct.unpack('>I',digest[offset:offset+4])[0]&0x7fffffff
    return str(number%(10**digits)).zfill(digits)


def matching_counter(secret, code, now=None, last_counter=-1):
    if not isinstance(code,str) or not re.fullmatch(r'[0-9]{6}',code.strip()):return None
    counter=int((time.time() if now is None else now)//30)
    try:
        for candidate in (counter,counter-1,counter+1):
            if candidate<0 or candidate<=last_counter:continue
            if hmac.compare_digest(totp(secret,candidate),code.strip()):return candidate
    except (ValueError,TypeError,base64.binascii.Error):return None
    return None


def recovery_hash(code):
    if not isinstance(code,str):return None
    normalized=code.replace('-','').replace(' ','').strip().lower()
    if not re.fullmatch(r'[a-f0-9]{20}',normalized):return None
    return hashlib.sha256(normalized.encode('ascii')).hexdigest()


def new_recovery_codes(count=10):
    values=[secrets.token_hex(10) for _ in range(count)]
    codes=['-'.join(value[i:i+5] for i in range(0,20,5)) for value in values]
    return codes,[recovery_hash(code) for code in codes]


def check_factor(cfg, code, now=None):
    """Return only persistence updates; the caller commits before granting access."""
    counter=matching_counter(cfg.get('mfa_secret',''),code,now,last_counter=cfg.get('mfa_last_counter',-1))
    if counter is not None:return dict(mfa_last_counter=counter)
    digest=recovery_hash(code)
    if digest:
        saved=cfg.get('mfa_recovery_hashes',[])
        for index,value in enumerate(saved):
            if hmac.compare_digest(digest,value):
                return dict(mfa_recovery_hashes=saved[:index]+saved[index+1:])
    return None


def provisioning_uri(secret, username):
    issuer='Antigravity Suite'
    label=quote(issuer+':'+username,safe='')
    return 'otpauth://totp/'+label+'?'+urlencode(dict(secret=secret,issuer=issuer,algorithm='SHA1',digits=6,period=30))
