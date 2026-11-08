/* __AGY_SESSION_GUARD_V2__ */
(() => {
    if (globalThis.__AGY_SESSION__?.version===2) return;
    const warning = '退出登录可能使已保存的登录凭据失效。请返回配置面板，使用“退出配置”。';
    async function request(operation, fields = {}) {
        if (globalThis.antigravitySuiteNative) return globalThis.antigravitySuiteNative.request(operation, fields);
        const routes = {begin:'/__suite/api/sessions/login-begin', complete:'/__suite/api/sessions/login-complete'};
        const response = await fetch(routes[operation], {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(fields)});
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || '配置面板暂时不可用，请稍后重试');
        return data;
    }
    globalThis.__AGY_SESSION__ = {
        version:2,
        begin: async () => (await request('begin')).intent_id,
        complete: async (intent_id, valid_auth) => {
            for(let attempt=0;attempt<5;attempt++) {
                try {
                    const data=await request('complete',{intent_id,valid_auth});
                    if(valid_auth && data.profile_id)setTimeout(()=>window.location.reload(),250);
                    return data;
                } catch {if(attempt<4)await new Promise(resolve=>setTimeout(resolve,1000));}
            }
            return null;
        },
        warn: async () => {
            if (globalThis.antigravitySuiteNative) {
                try {await globalThis.antigravitySuiteNative.warn();return;}catch {}
            }
            window.alert(warning);
        }
    };
    function accountSignOut(event) {
        const button=(event.composedPath?.()||[event.target]).find(node=>node?.matches?.('button,[role="button"],input[type="button"]'));
        if(!button)return false;
        const label=(button.getAttribute('aria-label')||button.textContent||button.value||'').trim().replace(/\s+/g,' ');
        if(!/^(?:sign\s*out|signing\s*out\.{3}|退出登录|退出登陆)$/i.test(label))return false;
        // Target the native Account/Email row; plugin disconnect and admin logout stay usable.
        for(let node=button.parentElement,depth=0;node&&depth<5;node=node.parentElement,depth++) {
            const text=node.textContent||'';
            if(/(?:\bemail\b|邮箱|电子邮件)/i.test(text) && /[^\s@]+@[^\s@]+/.test(text))return true;
        }
        return false;
    }
    function intercept(event) {
        if(event.type==='keydown' && !['Enter',' ','Spacebar'].includes(event.key))return;
        if(!accountSignOut(event))return;
        event.preventDefault();event.stopPropagation();event.stopImmediatePropagation();
        void globalThis.__AGY_SESSION__.warn();
    }
    // Runs before React's delegated handler, including keyboard-activated Sign Out.
    window.addEventListener('click',intercept,true);
    window.addEventListener('keydown',intercept,true);
    const originalFetch=window.fetch.bind(window);
    window.fetch=function(url,options){
        let path='';try{path=decodeURIComponent(new URL(typeof url==='string'?url:url.url,window.location.href).pathname);}catch{}
        if(/^\/exa\.language_server_pb\.LanguageServerService\/AuthLogout\/?$/i.test(path)) {
            void globalThis.__AGY_SESSION__.warn();return Promise.reject(new Error(warning));
        }
        return originalFetch(url,options);
    };
    if(globalThis.antigravitySuiteNative) {
        let attempts=0;
        const ready=()=>globalThis.antigravitySuiteNative.request('ready',{guard_version:2}).catch(()=>{if(++attempts<10)setTimeout(ready,1000);});
        void ready();
    }
})();
