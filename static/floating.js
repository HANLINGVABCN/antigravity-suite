/*
 * Antigravity Suite - 独立常驻悬浮控制面板 (Floating Management Dock)
 * 具备独立全生命周期管理、防崩溃热切换、多账号配置一键换装
 */

(function() {
    function escapeHtml(value) { const e=document.createElement('span'); e.textContent=String(value ?? ''); return e.innerHTML; }
    async function suiteFetch(url, options) {
        if (window.antigravitySuiteNative) {
            const operations={'/__suite/api/status':'status','/__suite/api/system/auth-status':'auth-status',
                '/__suite/api/profiles/switch':'switch','/__suite/api/system/restart':'restart','/__suite/api/sessions/exit':'exit'};
            const operation=operations[url];
            if(!operation)throw new Error('原生面板不支持此操作');
            const fields=options?.body?JSON.parse(options.body):{};
            const result=await window.antigravitySuiteNative.request(operation,fields);
            return {ok:true,json:async()=>result};
        }
        const response=await fetch(url, options);
        if (!response.ok) { const data=await response.clone().json().catch(() => ({})); throw new Error(data.error || '请求失败'); }
        return response;
    }
    if (window.__antigravity_suite_injected) return;
    window.__antigravity_suite_injected = true;

    // Inject Styles
    const style = document.createElement("style");
    style.textContent = `
        #ag-floating-btn {
            position: fixed;
            top: 40%;
            right: 0;
            width: 48px;
            height: 48px;
            border-radius: 50%;
            background: linear-gradient(135deg, #1e293b, #0f172a);
            border: 2px solid #38bdf8;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4), 0 0 12px rgba(56, 189, 248, 0.3);
            cursor: pointer;
            z-index: 999990;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: width 0.2s, background 0.2s;
            user-select: none;
            touch-action: none;
            box-sizing: border-box;
            outline-offset: 2px;
        }
        #ag-floating-btn.is-docked {
            width: 18px;
            border-radius: 12px 0 0 12px;
            border-right: 0;
            box-shadow: -2px 0 8px #0f172a66;
        }
        #ag-floating-btn.is-docked[data-side="left"] {
            border-radius: 0 12px 12px 0;
            border-left: 0;
            border-right: 2px solid #38bdf8;
            box-shadow: 2px 0 8px #0f172a66;
        }
        #ag-floating-btn.is-docked svg { display: none; }
        #ag-floating-btn .ag-edge-grip { display: none; }
        #ag-floating-btn.is-docked .ag-edge-grip { display: block; color: #38bdf8; font: 22px system-ui; }
        #ag-floating-btn.is-dragging { cursor: grabbing; transition: none; }
        #ag-floating-btn[aria-expanded="true"] { visibility: hidden; }
        #ag-floating-btn:focus-visible { outline: 2px solid #38bdf8; }
        #ag-floating-btn:hover {
            background: #1e293b;
        }
        #ag-floating-btn svg {
            width: 26px;
            height: 26px;
            fill: #38bdf8;
        }
        #ag-drawer-overlay {
            position: fixed;
            inset: 0;
            background: rgba(0, 0, 0, 0.6);
            backdrop-filter: blur(4px);
            z-index: 999991;
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.25s ease;
        }
        #ag-drawer-overlay.active {
            opacity: 1;
            pointer-events: auto;
        }
        #ag-drawer {
            position: fixed;
            top: 0;
            right: -380px;
            width: min(360px, calc(100vw - 16px));
            height: 100dvh;
            background: #0f172a;
            border-left: 1px solid #1e293b;
            color: #f8fafc;
            box-shadow: -8px 0 32px rgba(0, 0, 0, 0.5);
            z-index: 999992;
            display: flex;
            flex-direction: column;
            transition: right 0.3s cubic-bezier(0.16, 1, 0.3, 1);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        }
        #ag-drawer.active {
            right: 0;
        }
        #ag-drawer[data-side="left"] { left: -380px; right: auto; border-left: 0; border-right: 1px solid #1e293b; }
        #ag-drawer[data-side="left"].active { left: 0; }
        .ag-header {
            padding: 18px 20px;
            border-bottom: 1px solid #1e293b;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .ag-header h3 {
            margin: 0;
            font-size: 16px;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 8px;
            color: #38bdf8;
        }
        .ag-close-btn {
            background: transparent;
            border: none;
            color: #94a3b8;
            font-size: 20px;
            cursor: pointer;
            padding: 4px;
        }
        .ag-body {
            flex: 1;
            min-height: 0;
            overflow-y: auto;
            padding: 16px 20px;
        }
        .ag-card {
            background: #1e293b;
            border-radius: 8px;
            padding: 14px;
            margin-bottom: 16px;
            border: 1px solid #334155;
        }
        .ag-card-title {
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #94a3b8;
            margin-bottom: 8px;
            font-weight: 600;
        }
        .ag-status-item {
            display: flex;
            justify-content: space-between;
            margin-bottom: 6px;
            font-size: 13px;
        }
        .ag-status-val {
            color: #e2e8f0;
            font-weight: 500;
        }
        .ag-pill {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 999px;
            font-size: 11px;
            font-weight: 600;
        }
        .ag-pill-green { background: #065f46; color: #34d399; }
        .ag-pill-blue { background: #1e3a8a; color: #60a5fa; }
        .ag-profile-list {
            display: flex;
            flex-direction: column;
            gap: 10px;
        }
        .ag-profile-item {
            background: #0f172a;
            border: 1px solid #334155;
            border-radius: 6px;
            padding: 10px 12px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .ag-profile-item.active {
            border-color: #38bdf8;
            background: rgba(56, 189, 248, 0.05);
        }
        .ag-btn {
            padding: 6px 12px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 500;
            cursor: pointer;
            border: none;
            transition: all 0.2s;
        }
        .ag-btn-primary {
            background: #0284c7;
            color: #fff;
        }
        .ag-btn-primary:hover {
            background: #0369a1;
        }
        .ag-btn-secondary {
            background: #334155;
            color: #e2e8f0;
        }
        .ag-footer {
            padding: 14px 20px;
            border-top: 1px solid #1e293b;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        /* Loading Overlay for Graceful Restart */
        #ag-loading-mask {
            position: fixed;
            inset: 0;
            background: rgba(15, 23, 42, 0.88);
            backdrop-filter: blur(8px);
            z-index: 999999;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.3s ease;
        }
        #ag-loading-mask.active {
            opacity: 1;
            pointer-events: auto;
        }
        .ag-spinner {
            width: 48px;
            height: 48px;
            border: 3px solid rgba(56, 189, 248, 0.2);
            border-radius: 50%;
            border-top-color: #38bdf8;
            animation: ag-spin 0.8s linear infinite;
            margin-bottom: 16px;
        }
        @keyframes ag-spin {
            to { transform: rotate(360deg); }
        }
        .ag-loading-title {
            font-size: 18px;
            font-weight: 600;
            color: #f8fafc;
            margin-bottom: 8px;
        }
        .ag-loading-desc {
            font-size: 14px;
            color: #94a3b8;
        }
    `;
    document.head.appendChild(style);

    // Floating Button
    const btn = document.createElement("div");
    btn.id = "ag-floating-btn";
    btn.title = "点击打开控制中心，拖动调整位置";
    btn.classList.add('is-docked');
    btn.setAttribute('role','button');btn.tabIndex=0;
    btn.setAttribute('aria-label','打开 Antigravity 控制中心');
    btn.setAttribute('aria-controls','ag-drawer');btn.setAttribute('aria-expanded','false');
    btn.innerHTML = `
        <span class="ag-edge-grip" aria-hidden="true">‹</span>
        <svg viewBox="0 0 24 24">
            <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/>
        </svg>
    `;
    document.body.appendChild(btn);

    // Drawer & Mask
    const overlay = document.createElement("div");
    overlay.id = "ag-drawer-overlay";
    document.body.appendChild(overlay);

    const drawer = document.createElement("div");
    drawer.id = "ag-drawer";
    drawer.innerHTML = `
        <div class="ag-header">
            <h3>⚙️ Antigravity 控制中心</h3>
            <button class="ag-close-btn">&times;</button>
        </div>
        <div class="ag-body">
            <div class="ag-card">
                <div class="ag-card-title">当前运行状态</div>
                <div class="ag-status-item">
                    <span style="color:#94a3b8;">活跃配置:</span>
                    <span class="ag-status-val" id="ag-cur-name">加载中...</span>
                </div>
                <div class="ag-status-item">
                    <span style="color:#94a3b8;">Google 账号:</span>
                    <span class="ag-status-val" id="ag-cur-email">-</span>
                </div>
                <div class="ag-status-item">
                    <span style="color:#94a3b8;">出站网络:</span>
                    <span class="ag-status-val" id="ag-cur-proxy">-</span>
                </div>
            </div>

            <div class="ag-card">
                <div class="ag-card-title">快速切换配置</div>
                <div class="ag-profile-list" id="ag-profile-list">
                    <!-- Populated dynamically -->
                </div>
            </div>
        </div>
        <div class="ag-footer">
            <button class="ag-btn ag-btn-secondary" id="ag-btn-portal">🚪 返回完整配置大厅</button>
            <button class="ag-btn ag-btn-secondary" id="ag-btn-restart">🔄 重启当前核心引擎</button>
        </div>
    `;
    document.body.appendChild(drawer);

    const authNotice = document.createElement("div");
    authNotice.id = "ag-auth-notice";
    authNotice.style.cssText = "padding:10px 18px;font-size:12px;color:#fbbf24;line-height:1.5";
    drawer.insertBefore(authNotice, drawer.querySelector(".ag-footer"));
    // Loading Mask for Restarts
    const mask = document.createElement("div");
    mask.id = "ag-loading-mask";
    mask.innerHTML = `
        <div class="ag-spinner"></div>
        <div class="ag-loading-title" id="ag-mask-title">正在切换核心环境...</div>
        <div class="ag-loading-desc" id="ag-mask-desc">正在重启核心引擎，管理控制台保持可用...</div>
    `;
    document.body.appendChild(mask);

    // Events
    const positionKey='__agy_suite_dock_position_v1';
    let position={side:'right',y:0.4},drag=null,suppressClick=false;
    try {
        const saved=JSON.parse(localStorage.getItem(positionKey));
        if(saved&&['left','right'].includes(saved.side)&&Number.isFinite(saved.y))position={side:saved.side,y:Math.max(0,Math.min(1,saved.y))};
    } catch { /* A restricted browser may disable persistent storage. */ }
    function placeDock(){
        const travel=Math.max(0,innerHeight-64);
        btn.classList.add('is-docked');btn.classList.remove('is-dragging');
        btn.dataset.side=position.side;drawer.dataset.side=position.side;
        btn.style.top=(8+travel*position.y)+'px';btn.style.bottom='auto';
        btn.style.left=position.side==='left'?'0':'auto';btn.style.right=position.side==='right'?'0':'auto';
        btn.querySelector('.ag-edge-grip').textContent=position.side==='right'?'‹':'›';
    }
    function saveDock(){try{localStorage.setItem(positionKey,JSON.stringify(position));}catch{}}
    placeDock();
    btn.addEventListener('pointerdown',event=>{
        if(event.button!==0||!event.isPrimary)return;
        const rect=btn.getBoundingClientRect();
        drag={id:event.pointerId,startX:event.clientX,startY:event.clientY,x:rect.x,y:rect.y,moved:false};
        btn.setPointerCapture(event.pointerId);
    });
    btn.addEventListener('pointermove',event=>{
        if(!drag||event.pointerId!==drag.id)return;
        const dx=event.clientX-drag.startX,dy=event.clientY-drag.startY;
        if(!drag.moved&&Math.hypot(dx,dy)<6)return;
        drag.moved=true;event.preventDefault();
        btn.classList.remove('is-docked');btn.classList.add('is-dragging');
        btn.style.left=Math.max(0,Math.min(innerWidth-48,drag.x+dx))+'px';btn.style.right='auto';
        btn.style.top=Math.max(8,Math.min(innerHeight-56,drag.y+dy))+'px';
    });
    function finishDrag(event){
        if(!drag||event.pointerId!==drag.id)return;
        if(drag.moved){
            const rect=btn.getBoundingClientRect();
            position={side:rect.x+rect.width/2<innerWidth/2?'left':'right',y:Math.max(0,Math.min(1,(rect.y-8)/Math.max(1,innerHeight-64)))};
            suppressClick=true;setTimeout(()=>{suppressClick=false;},0);saveDock();
        }
        if(btn.hasPointerCapture(event.pointerId))btn.releasePointerCapture(event.pointerId);
        drag=null;placeDock();
    }
    btn.addEventListener('pointerup',finishDrag);btn.addEventListener('pointercancel',finishDrag);
    window.addEventListener('resize',()=>{drag=null;placeDock();});
    function openDrawer() {
        overlay.classList.add("active");
        drawer.classList.add("active");
        btn.setAttribute('aria-expanded','true');
        loadSuiteStatus();
        drawer.querySelector('.ag-close-btn').focus();
    }
    function closeDrawer() {
        overlay.classList.remove("active");
        drawer.classList.remove("active");
        btn.setAttribute('aria-expanded','false');placeDock();
    }

    btn.addEventListener('click',()=>{if(!suppressClick)openDrawer();});
    btn.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();openDrawer();}});
    document.addEventListener('keydown',event=>{if(event.key==='Escape'&&drawer.classList.contains('active')){closeDrawer();btn.focus();}});
    overlay.addEventListener("click", closeDrawer);
    drawer.querySelector(".ag-close-btn").addEventListener("click", closeDrawer);

    document.getElementById("ag-btn-portal").addEventListener("click", function() {
        if(window.antigravitySuiteNative)void window.antigravitySuiteNative.openPanel();
        else window.location.href = "/?portal=1";
    });

    document.getElementById("ag-btn-restart").addEventListener("click", function() {
        if (!confirm("确定要重启当前 Antigravity 引擎吗？")) return;
        triggerSwitch(null, "重启核心引擎");
    });
    async function exitCurrentProfile(profileId){
        closeDrawer();mask.classList.add('active');
        document.getElementById('ag-mask-title').textContent='正在退出当前配置…';
        document.getElementById('ag-mask-desc').textContent='保存存档，清理当前登录，并打开未登录界面。';
        try {
            await suiteFetch('/__suite/api/sessions/exit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({profile_id:profileId})});
            let attempts=0;
            const timer=setInterval(async()=>{
                try {
                    const data=await(await suiteFetch('/__suite/api/status')).json();
                    if(data.exit_state?.phase==='ready'){clearInterval(timer);window.location.reload();}
                    else if(data.exit_state?.phase==='error'){clearInterval(timer);mask.classList.remove('active');alert(data.exit_state.message);}
                } catch {}
                if(++attempts>90){clearInterval(timer);mask.classList.remove('active');alert('退出仍在进行，请返回配置面板查看状态。');}
            },1000);
        } catch(error){mask.classList.remove('active');alert(error.message);}
    }

    async function loadSuiteStatus() {
        try {
            const res = await suiteFetch("/__suite/api/status");
            const data = await res.json();

            document.getElementById("ag-cur-name").textContent = data.active_profile?.name || "未指定配置";
            document.getElementById("ag-cur-email").textContent = data.active_profile?.email || "未登录";

            const pBadge = document.getElementById("ag-cur-proxy");
            if (data.active_proxy) {
                pBadge.innerHTML = `<span class="ag-pill ag-pill-green">🛡️ ${escapeHtml(data.active_proxy.name)}</span>`;
            } else {
                pBadge.innerHTML = `<span class="ag-pill ag-pill-blue">🌐 本机直连</span>`;
            }

            if (data.antigravity_ready) {
                suiteFetch("/__suite/api/system/auth-status").then(res => res.json()).then(auth => {
                    authNotice.textContent = auth.reason;
                    authNotice.style.color = auth.authorized ? "#34d399" : "#fbbf24";
                }).catch(() => { authNotice.textContent = "暂时无法验证 Google 登录状态"; });
            }
            // Render profile list
            const listEl = document.getElementById("ag-profile-list");
            listEl.innerHTML = "";
            (data.profiles || []).forEach(p => {
                const isCur = data.active_profile && data.active_profile.id === p.id;
                const item = document.createElement("div");
                item.className = "ag-profile-item" + (isCur ? " active" : "");
                item.innerHTML = `
                    <div style="overflow:hidden;">
                        <div style="font-size:13px; font-weight:600; color:#f1f5f9; text-overflow:ellipsis; overflow:hidden; white-space:nowrap;">
                            ${escapeHtml(p.name)} ${isCur ? '<span style="color:#38bdf8; font-size:11px;">(当前)</span>' : ''}
                        </div>
                        <div style="font-size:11px; color:#94a3b8;">${escapeHtml(p.email || '未登录')}</div>
                    </div>
                    <div>
                        ${isCur ? `<button class="ag-btn ag-btn-primary" data-exit-profile="${p.id}" ${data.busy||data.starting_profile_id?'disabled':''}>退出配置</button>` : `<button class="ag-btn ag-btn-primary" data-id="${p.id}">切换</button>`}
                    </div>
                `;
                if (!isCur) {
                    item.querySelector("button").addEventListener("click", () => triggerSwitch(p.id, p.name));
                } else item.querySelector('button').addEventListener('click',()=>exitCurrentProfile(p.id));
                listEl.appendChild(item);
            });
        } catch (e) {
            console.error("[Suite Floating] Load status failed:", e);
        }
    }

    async function triggerSwitch(profileId, label) {
        closeDrawer();
        mask.classList.add("active");
        document.getElementById("ag-mask-title").textContent = `正在切换至 [${label}]...`;
        document.getElementById("ag-mask-desc").textContent = "正在同步凭据、切换出站代理并拉起新引擎...";

        try {
            const url = profileId ? "/__suite/api/profiles/switch" : "/__suite/api/system/restart";
            const body = profileId ? JSON.stringify({ profile_id: profileId }) : null;
            await suiteFetch(url, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: body
            });

            // Poll until ready
            let attempts = 0;
            const timer = setInterval(async () => {
                attempts++;
                try {
                    const chk = await suiteFetch("/__suite/api/status");
                    const st = await chk.json();
                    if (st.antigravity_ready && !st.starting_profile_id && (!profileId || st.active_profile?.id === profileId)) {
                        clearInterval(timer);
                        document.getElementById("ag-mask-title").textContent = "✅ 切换成功！";
                        document.getElementById("ag-mask-desc").textContent = "正在无缝刷新 Web IDE 视图...";
                        document.getElementById("ag-mask-desc").textContent = "启动已完成，点击下方按钮进入。";
                        const enter = document.createElement("button");
                        enter.className = "ag-btn ag-btn-primary"; enter.textContent = "进入";
                        enter.onclick = () => window.location.reload();
                        mask.appendChild(enter);
                    }
                } catch (e) {}

                if (attempts > 45) {
                    clearInterval(timer);
                    alert("服务启动超时，请刷新重试！");
                    mask.classList.remove("active");
                }
            }, 800);
        } catch (e) {
            alert("请求失败: " + e);
            mask.classList.remove("active");
        }
    }
})();
