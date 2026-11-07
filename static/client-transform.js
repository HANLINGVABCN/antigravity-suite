/* Native and gateway transformations are checked against the same fixture. */
function endOfMethod(source, start) {
    let depth = 0, quote = null, escaped = false;
    for (let i = source.indexOf('{', start); i < source.length; i++) {
        const char = source[i];
        if (quote) {
            if (escaped) escaped = false;
            else if (char === '\\') escaped = true;
            else if (char === quote) quote = null;
        } else if (['"', "'", '`'].includes(char)) quote = char;
        else if (char === '{') depth++;
        else if (char === '}' && --depth === 0) return i + 1;
    }
    throw new Error('无法识别客户端认证方法');
}
function transform(source, bootstrap = '', dock = '') {
    if (source.includes('__AGY_SESSION_GUARD_V1__')) return {source,supported:true};
    const logout = 'async logout(){', login = 'async loginWithRedirect(';
    if (source.split(logout).length !== 2 || source.split(login).length !== 2) return {source,supported:false};
    const a = source.indexOf(logout), aEnd = endOfMethod(source,a);
    if (!source.slice(a,aEnd).includes('.authLogout({})') || !source.slice(a,aEnd).includes('isLoggingOut')) return {source,supported:false};
    const b = source.indexOf(login), bEnd = endOfMethod(source,b);
    let method = source.slice(b,bEnd);
    const results=[...method.matchAll(/var ([A-Za-z_$][\w$]*)=([A-Za-z_$][\w$]*)\.authResult;/g)];
    if (!method.includes('.login({') || results.length!==1) return {source,supported:false};
    const result=results[0];
    if(!method.includes(result[1]+'?.hasValidAuth'))return {source,supported:false};
    const opening = method.indexOf('{');
    method = method.slice(0,opening+1)+'let __agyLoginEvent=await globalThis.__AGY_SESSION__.begin();let __agyLoginFinished=false;try{'+method.slice(opening+1);
    method = method.replace(result[0],result[0]+'__agyLoginFinished=true;await globalThis.__AGY_SESSION__.complete(__agyLoginEvent,!!'+result[1]+'?.hasValidAuth);');
    method=method.slice(0,-1)+'}finally{if(!__agyLoginFinished)await globalThis.__AGY_SESSION__.complete(__agyLoginEvent,false);}}';
    const replacements = [[b,bEnd,method],[a,a+logout.length,logout+'await globalThis.__AGY_SESSION__.warn();return;']].sort((x,y)=>y[0]-x[0]);
    for (const [start,end,value] of replacements) source=source.slice(0,start)+value+source.slice(end);
    const tail=dock?'\n(()=>{const install=()=>{'+dock+'};document.readyState==="loading"?document.addEventListener("DOMContentLoaded",install,{once:true}):install();})();':'';
    return {source:'/* __AGY_SESSION_GUARD_V1__ */\n'+bootstrap+'\n'+source+tail,supported:true};
}
function blockedLogout(url) {
    try { return /^\/exa\.language_server_pb\.LanguageServerService\/AuthLogout\/?$/i.test(decodeURIComponent(new URL(url).pathname)); }
    catch { return false; }
}
module.exports={transform,blockedLogout};
