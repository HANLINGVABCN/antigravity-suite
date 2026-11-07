"""Guard the verified native auth service, without blocking unrelated plugin sign-outs."""
import re
from urllib.parse import unquote,urlsplit

WARNING='退出登录可能使已保存的登录凭据失效。请返回配置面板，使用“退出配置”。'
LOGIN_ANCHOR='async loginWithRedirect('
LOGOUT_ANCHOR='async logout(){'


def blocks_logout(url):
    path=unquote(urlsplit(str(url)).path).rstrip('/')
    return bool(re.fullmatch(r'/exa\.language_server_pb\.LanguageServerService/AuthLogout',path,re.I))


def method_end(source,start):
    opening=source.index('{',start);depth=0;quote=None;escape=False;i=opening
    while i<len(source):
        char=source[i]
        if quote:
            if escape:escape=False
            elif char=='\\':escape=True
            elif char==quote:quote=None
        elif char in ('"',"'",'`'):quote=char
        elif char=='{':depth+=1
        elif char=='}':
            depth-=1
            if not depth:return i+1
        i+=1
    raise ValueError('无法识别客户端认证方法')


def transform_client(source, bootstrap='', dock=''):
    if '__AGY_SESSION_GUARD_V1__' in source:return source,True
    if source.count(LOGOUT_ANCHOR)!=1 or source.count(LOGIN_ANCHOR)!=1:return source,False
    logout_start=source.index(LOGOUT_ANCHOR);logout_end=method_end(source,logout_start)
    if '.authLogout({})' not in source[logout_start:logout_end] or 'isLoggingOut' not in source[logout_start:logout_end]:return source,False
    login_start=source.index(LOGIN_ANCHOR);login_end=method_end(source,login_start)
    login=source[login_start:login_end]
    results=list(re.finditer(r'var ([A-Za-z_$][\w$]*)=([A-Za-z_$][\w$]*)\.authResult;',login))
    if '.login({' not in login or len(results)!=1:return source,False
    result=results[0]
    if not re.search(r'\b'+re.escape(result.group(1))+r'\?\.hasValidAuth\b',login):return source,False
    opening=login.index('{')
    login=login[:opening+1]+'let __agyLoginEvent=await globalThis.__AGY_SESSION__.begin();let __agyLoginFinished=false;try{'+login[opening+1:]
    login=login.replace(result.group(0),result.group(0)+'__agyLoginFinished=true;await globalThis.__AGY_SESSION__.complete(__agyLoginEvent,!!'+result.group(1)+'?.hasValidAuth);',1)
    login=login[:-1]+'}finally{if(!__agyLoginFinished)await globalThis.__AGY_SESSION__.complete(__agyLoginEvent,false);}}'
    replacements=[(login_start,login_end,login),(logout_start,logout_start+len(LOGOUT_ANCHOR),LOGOUT_ANCHOR+'await globalThis.__AGY_SESSION__.warn();return;')]
    for start,end,text in sorted(replacements,reverse=True):source=source[:start]+text+source[end:]
    tail=''
    if dock:tail='\n(()=>{const install=()=>{'+dock+'};document.readyState==="loading"?document.addEventListener("DOMContentLoaded",install,{once:true}):install();})();'
    return '/* __AGY_SESSION_GUARD_V1__ */\n'+bootstrap+'\n'+source+tail,True
