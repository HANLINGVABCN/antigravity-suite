/* Loaded before the native application. CSP is the primary network boundary. */
(() => {
    'use strict';
    const profile = window.__SUITE_PROFILE_ID__ || 'none';
    const prefix = '__suite_profile_' + profile + ':';
    const cookiePrefix = 'agp_' + profile + '_';
    const own = url => {
        try { const u = new URL(String(url), location.href); return u.origin === location.origin || u.protocol === 'blob:' || u.protocol === 'data:'; }
        catch { return false; }
    };
    const notice = () => {
        let box = document.getElementById('ag-privacy-blocked');
        if (!box && document.body) {
            box = document.createElement('div'); box.id = 'ag-privacy-blocked';
            box.style.cssText = 'position:fixed;bottom:74px;right:18px;max-width:360px;padding:12px;background:#172033;color:#fbbf24;border:1px solid #f59e0b;border-radius:8px;z-index:2147483646;font:13px sans-serif';
            box.textContent = '隐私保护已拦截外部链接。需要授权账号时，请返回配置大厅操作。';
            document.body.appendChild(box); setTimeout(() => box.remove(), 5000);
        }
    };
    const originalOpen = window.open.bind(window);
    window.open = function(url, ...args) { if (!own(url)) { notice(); return null; } return originalOpen(url, ...args); };
    document.addEventListener('click', event => {
        const anchor = event.target.closest?.('a[href]');
        if (anchor && !own(anchor.href)) { event.preventDefault(); event.stopImmediatePropagation(); notice(); }
    }, true);
    // WebRTC can bypass HTTP proxy settings and reveal network addresses.
    for (const key of ['RTCPeerConnection', 'webkitRTCPeerConnection']) {
        try { Object.defineProperty(window, key, {value: undefined, configurable: false}); } catch {}
    }
    try {
        const descriptor=Object.getOwnPropertyDescriptor(Document.prototype,'cookie');
        if (descriptor?.get && descriptor?.set) Object.defineProperty(document,'cookie',{
            get:() => descriptor.get.call(document).split(';').map(v => v.trim()).filter(v => v.startsWith(cookiePrefix)).map(v => v.slice(cookiePrefix.length)).join('; '),
            set:value => descriptor.set.call(document,cookiePrefix+value)
        });
    } catch {}
    // Keep browser-side account data separate on an arbitrary reverse-proxy origin.
    for (const type of ['localStorage', 'sessionStorage']) {
        try {
            const backing = window[type];
            const keys = () => Array.from({length:backing.length}, (_,i) => backing.key(i)).filter(k => k?.startsWith(prefix));
            const api = {
                getItem:key => backing.getItem(prefix + key),
                setItem:(key,value) => backing.setItem(prefix + key, String(value)),
                removeItem:key => backing.removeItem(prefix + key),
                clear:() => keys().forEach(k => backing.removeItem(k)),
                key:index => keys()[index]?.slice(prefix.length) ?? null
            };
            Object.defineProperty(api, 'length', {get:() => keys().length});
            const facade = new Proxy(api, {
                get:(obj,key) => key in obj || typeof key === 'symbol' ? obj[key] : api.getItem(key),
                set:(_,key,value) => {api.setItem(key,value); return true;},
                deleteProperty:(_,key) => {api.removeItem(key); return true;}
            });
            Object.defineProperty(window, type, {get:() => facade});
        } catch { /* Storage unavailable in restrictive browser modes. */ }
    }
    if (window.indexedDB) {
        for (const method of ['open', 'deleteDatabase']) {
            const original = indexedDB[method].bind(indexedDB);
            indexedDB[method] = (name, ...args) => original(prefix + name, ...args);
        }
        if (indexedDB.databases) {
            const original = indexedDB.databases.bind(indexedDB);
            indexedDB.databases = async () => (await original()).filter(d => d.name?.startsWith(prefix)).map(d => ({...d,name:d.name.slice(prefix.length)}));
        }
    }
    if (window.caches) {
        for (const method of ['open', 'has', 'delete']) {
            const original = caches[method].bind(caches);
            caches[method] = (name, ...args) => original(prefix + name, ...args);
        }
        const originalKeys = caches.keys.bind(caches);
        caches.keys = async () => (await originalKeys()).filter(k => k.startsWith(prefix)).map(k => k.slice(prefix.length));
        const originalMatch = caches.match.bind(caches);
        caches.match = (request,options={}) => originalMatch(request,{...options,cacheName:prefix + (options.cacheName || 'default')});
    }
    window.__SUITE_PRIVACY__ = {enabled:true,profile};
})();
