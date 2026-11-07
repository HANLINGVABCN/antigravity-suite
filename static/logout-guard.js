/* __AGY_SESSION_GUARD_V1__ */
(() => {
    if (globalThis.__AGY_SESSION__) return;
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
            if (globalThis.antigravitySuiteNative) await globalThis.antigravitySuiteNative.warn();
            else window.alert(warning);
            if (document.getElementById('ag-floating-btn')) document.getElementById('ag-floating-btn').click();
        }
    };
    const originalFetch=window.fetch.bind(window);
    window.fetch=function(url,options){
        let path='';try{path=new URL(typeof url==='string'?url:url.url,window.location.href).pathname;}catch{}
        if(/^\/exa\.language_server_pb\.LanguageServerService\/AuthLogout\/?$/i.test(path)) {
            void globalThis.__AGY_SESSION__.warn();return Promise.reject(new Error(warning));
        }
        return originalFetch(url,options);
    };
    if(globalThis.antigravitySuiteNative) {
        let attempts=0;
        const ready=()=>globalThis.antigravitySuiteNative.request('ready').catch(()=>{if(++attempts<10)setTimeout(ready,1000);});
        void ready();
    }
})();
