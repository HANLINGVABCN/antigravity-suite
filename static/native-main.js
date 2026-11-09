const {app,session,protocol,ipcMain,shell,webContents}=require('electron');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),http=require('node:http'),https=require('node:https'),zlib=require('node:zlib');
const {transform,blockedLogout}=require('./suite-client-transform.js');
const bootstrap=__AGY_BOOTSTRAP__;
const dock=__AGY_DOCK__;
const warning='退出登录可能使已保存的登录凭据失效。请返回配置面板，使用“退出配置”。';
const guarded=new Map(),roots=new Map(),allowedPorts=new Set(),installedSessions=new WeakSet();
let registered=false;
function local(url) {
    try { const u=new URL(url);return ['127.0.0.1','localhost','[::1]'].includes(u.hostname)?u:null; }
    catch { return null; }
}
function configuration() {
    const file=path.join(process.env.HOME||os.homedir(),'.cache','antigravity-suite','native-control.json');
    const text=fs.readFileSync(file,'utf8');
    if (text.length>4096) throw new Error('原生配置面板信息无效');
    const value=JSON.parse(text),url=local(value.url);
    if (!url || url.protocol!=='http:' || !/^[a-f0-9]{32}$/.test(value.runtime_id) || !/^[a-f0-9]{64}$/.test(value.token)) throw new Error('原生配置面板信息无效');
    return value;
}
function fetchBytes(url,options={}) {
    return new Promise((resolve,reject)=>{
        const client=url.startsWith('https:')?https:http;
        const req=client.request(url,{...options,rejectUnauthorized:false,timeout:15000},res=>{
            const chunks=[];let bytes=0;
            res.on('data',chunk=>{bytes+=chunk.length;if(bytes>32*1024*1024){req.destroy(new Error('响应超过大小限制'));return;}chunks.push(chunk);});
            res.on('end',()=>{
                try {
                    let body=Buffer.concat(chunks);
                    if (res.headers['content-encoding']==='gzip') body=zlib.gunzipSync(body,{maxOutputLength:32*1024*1024});
                    if (res.headers['content-encoding']==='br') body=zlib.brotliDecompressSync(body,{maxOutputLength:32*1024*1024});
                    if (res.statusCode<200||res.statusCode>=300) throw new Error('配置面板请求失败');
                    resolve(body);
                } catch(error) {reject(error);}
            });
            res.on('error',reject);
        });
        req.on('timeout',()=>req.destroy(new Error('配置面板请求超时')));req.on('error',reject);
        if(options.body)req.write(options.body);req.end();
    });
}
function trusted(event) {
    const expected=guarded.get(event.sender.id),actual=local(event.sender.getURL());
    if (!expected || !actual || actual.origin!==expected || event.senderFrame!==event.sender.mainFrame) throw new Error('此窗口没有原生面板权限');
}
async function control(event,operation,fields={}) {
    trusted(event);
    if (!['status','auth-status','switch','restart','exit','begin','complete','ready'].includes(operation)) throw new Error('原生操作不支持');
    if (!fields || typeof fields!=='object' || Array.isArray(fields)) throw new Error('操作参数无效');
    const cfg=configuration();
    const body=JSON.stringify({runtime_id:cfg.runtime_id,operation,fields});
    const result=JSON.parse((await fetchBytes(cfg.url+'/__suite/native/control',{
        method:'POST',body,headers:{'Content-Type':'application/json','Content-Length':Buffer.byteLength(body),'X-Antigravity-Native-Control':cfg.token}
    })).toString());
    if (!result.ok) throw new Error(result.error||'配置面板操作失败');
    return result;
}
async function warn(event,identifier) {
    if(event)trusted(event);
    const target=event?.sender||webContents.fromId(identifier);
    if(!target || !roots.has(target.id))return;
    // Use a renderer dialog: GTK system dialogs can crash in restricted desktop containers.
    await target.executeJavaScript(`(()=>{let d=document.getElementById('agy-logout-warning');if(!d){d=document.createElement('dialog');d.id='agy-logout-warning';d.style.cssText='max-width:440px;padding:24px;border:2px solid #f59e0b;border-radius:12px;background:#172033;color:#f8fafc;z-index:2147483647;line-height:1.7';const p=document.createElement('p');p.textContent=${JSON.stringify(warning)};const b=document.createElement('button');b.textContent='确定';b.style.cssText='padding:10px 18px;margin-top:16px;cursor:pointer';b.onclick=()=>d.close();d.append(p,b);document.body.append(d)}if(!d.open)d.showModal()})();`);
}
function installSession(s) {
    if(installedSessions.has(s))return;installedSessions.add(s);
    s.protocol.handle('agy-suite',async request=>{
        const u=new URL(request.url),match=/^\/(\d+)\/main\.js$/.exec(u.pathname),id=Number(u.searchParams.get('window'));
        const root=roots.get(id);
        if (u.hostname!=='client' || !match || !allowedPorts.has(match[1]) || !root || new URL(root).port!==match[1]) return new Response('拒绝请求',{status:403});
        try {
            const original=(await fetchBytes('https://127.0.0.1:'+match[1]+'/main.js')).toString();
            const result=transform(original,bootstrap,dock);
            if(!result.supported) return new Response(original,{headers:{'Content-Type':'application/javascript'}});
            guarded.set(id,root);
            return new Response(result.source,{headers:{'Content-Type':'application/javascript','Cache-Control':'no-store'}});
        } catch {return new Response('throw new Error("原生退出保护加载失败，请返回配置面板");',{headers:{'Content-Type':'application/javascript'}});}
    });
    s.webRequest.onBeforeRequest({urls:['https://127.0.0.1:*/*','https://localhost:*/*']},(details,callback)=>{
        const u=local(details.url);
        if (u && allowedPorts.has(u.port) && blockedLogout(u.href)) {
            callback({cancel:true});void warn(null,details.webContentsId).catch(()=>{});return;
        }
        if (u && u.pathname==='/main.js' && u.protocol==='https:' && details.resourceType==='script') {
            const root=roots.get(details.webContentsId);
            if (root && root===u.origin) {
                allowedPorts.add(u.port);
                callback({redirectURL:'agy-suite://client/'+u.port+'/main.js?window='+details.webContentsId});return;
            }
        }
        callback({});
    });
}
function registerWindow(win,url) {
    const origin=local(url);
    if(!origin || origin.protocol!=='https:' || origin.pathname!=='/')return;
    roots.set(win.webContents.id,origin.origin);allowedPorts.add(origin.port);
    win.webContents.once('destroyed',()=>{roots.delete(win.webContents.id);guarded.delete(win.webContents.id);});
    if(app.isReady())installSession(win.webContents.session);
    else app.whenReady().then(()=>installSession(win.webContents.session));
}
function install() {
    if(registered)return;registered=true;
    protocol.registerSchemesAsPrivileged([{scheme:'agy-suite',privileges:{standard:true,secure:true,bypassCSP:true,supportFetchAPI:true}}]);
    app.on('session-created',s=>{if(app.isReady())installSession(s);else app.whenReady().then(()=>installSession(s));});
    app.on('window-all-closed',()=>{
        // Closing the last visible desktop window is an explicit stop, even if
        // the vendor's run-in-background preference is enabled.
        if(process.env.ELECTRON_OZONE_PLATFORM_HINT!=='headless')app.quit();
    });
    app.whenReady().then(()=>installSession(session.defaultSession));
    ipcMain.handle('agy-suite:control',control);
    ipcMain.handle('agy-suite:warning',event=>warn(event));
    ipcMain.handle('agy-suite:open-panel',async event=>{trusted(event);await shell.openExternal(configuration().url+'/?portal=1');return true;});
}
module.exports={install,registerWindow};
