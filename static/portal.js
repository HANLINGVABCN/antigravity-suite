let currentStatus = null;
        let coreStatus = null;
        let coreInstallPollTimer = null;
        let currentEditingProfileId = null;
        let currentEditingProxyId = null;
        let originalProxyLink = "";
        let proxyNameBeforeEdit = "";
        let exportProfileId = null;
        let selectedExportFormat = 'suite';
        let pendingProfileImport = null;
        let selectedImportSource = 'suite';
        let importingProfile = false;

        let settingsOriginalUser = "";
        async function openServiceSettings() {
            await cancelAuthenticatorSetup();
            const data = await (await fetch("/__suite/api/system/config")).json();
            settingsOriginalUser = data.auth_user;
            document.getElementById("settings-port").value = data.listen_port;
            document.getElementById("settings-user").value = data.auth_user;
            document.getElementById("settings-current").value = "";
            document.getElementById("settings-new").value = "";
            clearRecoveryDisplay();
            document.getElementById("mfa-current-code").value = "";
            await loadAuthenticatorStatus();
            await loadServiceRestartStatus();
            PortalUI.open("modal-service-settings");
        }
        async function saveServiceSettings() {
            const user = document.getElementById("settings-user").value.trim();
            const password = document.getElementById("settings-new").value;
            const result = await (await fetch("/__suite/api/system/config", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
                listen_port:Number(document.getElementById("settings-port").value),auth_user:user,
                current_password:document.getElementById("settings-current").value,auth_pass:password,
                code:document.getElementById("mfa-current-code").value.trim()})})).json();
            closeModals();
            if (result.restart_required) alert("设置已保存。重新打开“服务设置”并点击“重启后台服务”后，新端口才生效；请同步调整反代或 SSH 转发的目标端口。");
            if (password || user !== settingsOriginalUser) window.location.href = "/";
        }


        let localStartingId = null;
        let requestingLogin = false;
        let loginEntryError = '';
        function diagnosticMarkup(profile) {
            const result = currentStatus.diagnostics?.[profile.id];
            if (!result) return '<div class="diag pending">尚未检测</div>';
            const stamp = result.checked_at ? new Date(result.checked_at * 1000).toLocaleString() : '';
            const label = result.status === 'normal' ? '检测正常' : result.status === 'checking' ? '检测中…' : '异常 · 请检查';
            const elapsed=result.status==='checking'&&result.started_at?'已用 '+Math.max(0,Math.floor(Date.now()/1000-result.started_at))+' 秒':stamp;
            return `<div class="diag ${result.status}"><strong>${label}</strong><div>${escapeHtml(result.reason)}</div><small>${escapeHtml(elapsed)}</small></div>`;
        }
        function showSummary() {
            SuiteCoreToggle.render(currentStatus,coreStatus);
            const notice = document.getElementById("google-auth-notice");
            const message=document.getElementById('google-auth-text');
            const entry=document.getElementById('btn-open-login');
            const loggedOut=currentStatus?.native_session?.state==='logged_out';
            entry.hidden=!loggedOut;
            entry.disabled=requestingLogin||!!currentStatus?.opening_login||!!currentStatus?.busy;
            entry.textContent=requestingLogin||currentStatus?.opening_login?'正在打开登录界面…':currentStatus?.login_environment_ready?'进入登录界面':'打开登录界面';
            const protection=currentStatus?.logout_protection;
            const guardStatus=document.getElementById('logout-guard-status');
            guardStatus.textContent=protection?.renderer_ready?'桌面退出保护已验证。每次重新登录单独存档，退出请用“退出配置”。':
                protection?.installed?'桌面退出保护已安装，等待客户端验证。退出请用“退出配置”。':(protection?.reason||'正在检查桌面退出保护');
            const phase=currentStatus?.exit_state?.phase;
            const exiting=['queued','saving','opening'].includes(phase);
            if(exiting || phase==='error') {
                notice.className='summary '+(phase==='error'?'abnormal':'pending');
                message.textContent=currentStatus.exit_state.message;return;
            }
            if (currentStatus?.native_session?.state === 'logged_out') {
                notice.className = 'summary pending';
                message.textContent = '反重力当前未登录。已保存的账号配置仍保留，可以选择配置重新启动，或打开登录界面登录另一个账号。';
                if(currentStatus.opening_login)message.textContent+=' 正在启动登录界面，请稍候。';
                else if(currentStatus.last_error&&!currentStatus.login_environment_ready)message.textContent+=' 可能是登录环境启动异常：'+currentStatus.last_error;
                if(loginEntryError)message.textContent+=' '+loginEntryError;
                return;
            }
            const result = currentStatus?.diagnostics?.[currentStatus.active_profile?.id];
            notice.className = 'summary ' + (result?.status || 'pending');
            message.textContent = result ? ((result.status === 'abnormal' ? '异常：' : '') + result.reason) :
                '隐私保护已启用：IDE 网页禁止外部直连，登录存档各自保留，聊天记录共享。点击档案的“检测”检查账号状态。';
        }
        async function openNativeLogin(){
            if(requestingLogin)return;
            requestingLogin=true;loginEntryError='';showSummary();
            try{
                const status=await(await fetch('/__suite/api/status',{cache:'no-store'})).json();
                if(status.native_session?.state!=='logged_out'){await fetchStatus();return;}
                if(status.login_environment_ready&&!status.opening_login&&!status.busy){location.href='/';return;}
                await fetch('/__suite/api/system/start',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
                await fetchStatus();
            }catch(error){loginEntryError='登录界面暂未打开：'+error.message;}
            finally{requestingLogin=false;showSummary();}
        }
        async function detectProfile(id) {
            await fetch("/__suite/api/profiles/detect", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({profile_id:id})});
            await fetchStatus();
        }

        function escapeHtml(value) {
            const element = document.createElement("span");
            element.textContent = String(value ?? "");
            return element.innerHTML;
        }
        const originalFetch = window.fetch.bind(window);
        window.fetch = async function(url, options) {
            const response = await originalFetch(url, options);
            if (String(url).startsWith("/__suite/") && !response.ok) {
                const data = await response.clone().json().catch(() => ({}));
                if (response.status === 401) window.location.href = "/";
                throw new Error(data.error || "请求失败 (" + response.status + ")");
            }
            return response;
        };
        window.addEventListener("unhandledrejection", event => {
            event.preventDefault(); alert(event.reason?.message || "操作失败");
        });
        async function stopEngine() {
            await SuiteCoreToggle.toggle();
        }
        async function exitCurrentSession(profileId) {
            if(profileId!==currentStatus?.active_profile?.id){alert('当前配置已变化，请重新选择当前卡片。');return;}
            await fetch('/__suite/api/sessions/exit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({profile_id:profileId})});
            await fetchStatus();
        }
        async function editProfileFile(id, path) {
            const data = await (await fetch("/__suite/api/profiles/file?id=" + encodeURIComponent(id) + "&path=" + encodeURIComponent(path))).json();
            document.getElementById("file-editor-title").textContent = path;
            document.getElementById("file-editor-content").value = data.content;
            document.getElementById("file-editor-save").onclick = async () => {
                await fetch("/__suite/api/profiles/file", {method:"POST",headers:{"Content-Type":"application/json"},
                    body:JSON.stringify({id,path,content:document.getElementById("file-editor-content").value})});
                PortalUI.close("modal-file-editor");
            };
            PortalUI.open("modal-file-editor");
        }


        let statusRequest = null, refreshRequested = false;
        async function fetchStatus() {
            if (statusRequest) { refreshRequested = true; return statusRequest; }
            statusRequest = (async () => {
                try {
                    const res = await fetch("/__suite/api/status");
                    currentStatus = await res.json();
                    renderUI();
                    await fetchCoreStatus();
                    showSummary();
                } catch (error) { console.error("加载状态异常", error); }
                finally {
                    statusRequest = null;
                    if (refreshRequested) { refreshRequested = false; setTimeout(fetchStatus, 0); }
                }
            })();
            return statusRequest;
        }

        async function fetchCoreStatus() {
            try {
                const res = await fetch("/__suite/api/system/core-status");
                coreStatus = await res.json();
                updateCoreBadge();
            } catch(e) {}
        }

        function updateCoreBadge() {
            const pill = document.getElementById("pill-core-status");
            if (!pill || !coreStatus) return;
            const action = document.getElementById('btn-core-manage');
            action.disabled = false; action.textContent = coreStatus.installed ? '卸载反重力' : '安装反重力';
            if (coreStatus.installed) {
                pill.className = "pill pill-active";
                pill.innerHTML = `🟢 反重力本体: 已就绪 (${coreStatus.size_mb}MB)`;
                pill.style.cursor = "pointer";
                pill.onclick = () => openCoreInstallModal();
            } else {
                pill.className = "pill";
                pill.style.borderColor = "#f59e0b";
                pill.style.color = "#f59e0b";
                pill.innerHTML = `🟡 反重力本体: 未安装 (点击安装)`;
                pill.style.cursor = "pointer";
                pill.onclick = () => openCoreInstallModal();
            }
        }

        function renderUI() {
            if (!currentStatus) return;

            // System pills
            const sysPill = document.getElementById("pill-system-status");
            sysPill.textContent = `💻 内存: ${currentStatus.system?.mem_used || '-'} / Swap: ${currentStatus.system?.swap_used || '-'}`;

            const ipPill = document.getElementById("pill-cur-ip");
            if (currentStatus.active_proxy) {
                ipPill.className = "pill pill-active";
                ipPill.textContent = `🛡️ 出口: ${currentStatus.active_proxy.name}`;
            } else {
                ipPill.className = "pill";
                ipPill.textContent = `🌐 出口: 本机直连`;
            }

            if (!PortalUI.canRenderCards()) return;

            // Profiles
            const pCont = document.getElementById("profile-container");
            const profileCards = [];
            const profiles = (currentStatus.profiles || []).filter(p => !p.internal_runtime && p.kind !== 'existing');
            if (profiles.length === 0) {
                // The stable list renderer shows the empty state.
            } else {
                profiles.forEach(p => {
                    const isCur = currentStatus.active_profile?.id === p.id;
                    const isStarting = currentStatus.starting_profile_id === p.id || localStartingId === p.id;
                    const ready = isCur && currentStatus.antigravity_ready && !isStarting;
                    const detecting = currentStatus.diagnostics?.[p.id]?.status === 'checking';
                    const isDef = currentStatus.default_profile_id === p.id;
                    const boundProxy = (currentStatus.proxies || []).find(px => px.id === p.proxy_id);

                    const div = document.createElement("div");
                    div.className = "card-item" + (isCur ? " active-item" : "");
                    div.innerHTML = `
                        <div class="card-head">
                            <div class="card-title">
                                ${escapeHtml(p.name)}
                                ${isCur && currentStatus.antigravity_ready ? '<span class="pill pill-active" style="padding:2px 8px; font-size:10px;">当前运行</span>' : ''}
                                ${isDef ? '<span class="tag-default">开机默认</span>' : ''}
                            </div>
                            <div style="font-size:12px; color:var(--text-sub);">
                                ${p.email ? '📧 ' + escapeHtml(p.email) : '未登录'}
                            </div>
                        </div>
                        <div class="card-meta">
                            <span>🛡️ 绑定网络: <b>${boundProxy ? escapeHtml(boundProxy.name) : '本机直连'}</b></span>
                            <span>🕒 上次活跃: ${escapeHtml(p.last_used_at || p.created_at)}</span>
                        </div>
                        ${diagnosticMarkup(p)}
                        <div class="card-actions">
                            <button class="btn btn-primary" data-launch-id="${p.id}" onclick="profileAction('${p.id}')" ${isStarting || (currentStatus.starting_profile_id && !ready) ? 'disabled' : ''}>${isStarting ? '启动中…' : ready ? '进入' : '切换并启动'}</button>
                            <button class="btn btn-secondary" onclick="detectProfile('${p.id}')" ${detecting ? 'disabled' : ''}>${detecting ? '检测中…' : '检测'}</button>
                            <button class="btn btn-secondary" onclick="openEditModal('${p.id}')">📝 详情与编辑</button>
                            <button class="btn btn-secondary" onclick="toggleDefault('${p.id}')">${isDef ? '取消默认' : '⭐️ 设为开机默认'}</button>
                            <button class="btn btn-secondary" onclick="exportProfile('${p.id}')">📤 导出</button>
                            ${isCur?`<button class="btn btn-danger" id="btn-exit-session" data-exit-profile="${p.id}" onclick="exitCurrentSession('${p.id}')" ${currentStatus.busy||currentStatus.starting_profile_id||['queued','saving','opening'].includes(currentStatus.exit_state?.phase)?'disabled':''}>${['queued','saving','opening'].includes(currentStatus.exit_state?.phase)?'正在退出…':'退出配置'}</button>`:''}
                            <button class="btn btn-danger" onclick="deleteProfile('${p.id}')">🗑️ 删除</button>
                        </div>
                    `;
                    profileCards.push({id:p.id, className:div.className, html:div.innerHTML});
                });
            }

            PortalUI.updateCards(pCont, profileCards, "暂无配置档案，请新建、登录或导入。");

            // Proxies
            const pxCont = document.getElementById("proxy-container");
            const proxyCards = [];
            const proxies = currentStatus.proxies || [];
            if (proxies.length === 0) {
                // The stable list renderer shows the empty state.
            } else {
                proxies.forEach(px => {
                    const div = document.createElement("div");
                    div.className = "card-item";
                    div.innerHTML = `
                        <div class="card-head">
                            <div class="card-title">
                                🛡️ ${escapeHtml(px.name)}
                                <span class="pill" style="padding:2px 8px; font-size:10px; text-transform:uppercase;">${escapeHtml(px.protocol==='socks5h'?'socks5':px.protocol)}</span>
                            </div>
                            <button class="btn btn-danger" style="padding:4px 8px; font-size:11px;" onclick="deleteProxy('${px.id}')">删除</button>
                        </div>
                        <div class="card-meta">
                            <span>地址: ${escapeHtml(px.host)}:${escapeHtml(px.port)}</span>
                            <span>鉴权: ${px.username ? '账号密码保护' : '免密'}</span>
                            <span>ID: ${escapeHtml(px.id)}</span>
                        </div>
                        <div class="card-actions">
                            <button class="btn btn-secondary" onclick="openEditProxy('${px.id}')">详情与编辑</button>
                            <button class="btn btn-secondary" style="font-size:12px; padding:4px 10px;" id="test-btn-${px.id}" onclick="testProxy('${px.id}')">⚡ 测试连通性与出口 IP</button>
                            <span id="test-result-${px.id}" style="font-size:12px; color:var(--text-sub);"></span>
                        </div>
                    `;
                    proxyCards.push({id:px.id, className:div.className, html:div.innerHTML});
                });
            }

            PortalUI.updateCards(pxCont, proxyCards, "代理池为空，可添加 HTTP / HTTPS / SOCKS5 节点。");

            // Update modal selects
            updateProxySelects(proxies);
        }

        function updateProxySelects(proxies) {
            ['prof-in-proxy', 'oauth-prof-proxy', 'edit-prof-proxy'].forEach(selId => {
                const sel = document.getElementById(selId);
                if (!sel) return;
                if (PortalUI.hasOpenDialog()) return;
                const selected = sel.value;
                sel.innerHTML = `<option value="">🌐 直连（不使用代理）</option>`;
                proxies.forEach(px => {
                    sel.innerHTML += `<option value="${px.id}">🛡️ ${escapeHtml(px.name)} (${escapeHtml(px.host)}:${escapeHtml(px.port)})</option>`;
                });
                if ([...sel.options].some(option => option.value === selected)) sel.value = selected;
            });
        }

        // Modals
        function openAddProxyModal() {
            currentEditingProxyId = null;
            originalProxyLink = "";
            proxyNameBeforeEdit = "";
            document.getElementById("proxy-modal-title").textContent = "添加出站代理节点";
            document.getElementById("proxy-identity").style.display = "none";
            document.getElementById("proxy-password-actions").style.display = "none";
            document.getElementById("proxy-links-label").textContent = "节点链接（每行一个）";
            for (const id of ["proxy-links","proxy-in-name","proxy-in-host","proxy-in-port","proxy-in-user","proxy-in-pass"]) document.getElementById(id).value = "";
            const protocolSelect=document.getElementById("proxy-in-proto");
            protocolSelect.querySelector('[data-legacy]')?.remove();
            delete protocolSelect.dataset.savedProtocol;
            protocolSelect.value = "socks5";
            document.getElementById("proxy-in-pass").type = "password";
            document.getElementById("proxy-in-pass").placeholder = "可留空";
            document.getElementById("proxy-clear-password").checked = false;
            document.getElementById("proxy-save-button").textContent = "保存节点";
            PortalUI.open("modal-proxy");
        }
        async function openEditProxy(id) {
            const data = await (await fetch("/__suite/api/proxies/detail?id=" + encodeURIComponent(id) + "&include_link=1")).json();
            const proxy = data.proxy;
            openAddProxyModal();
            currentEditingProxyId = proxy.id;
            document.getElementById("proxy-modal-title").textContent = "节点详情与编辑";
            document.getElementById("proxy-identity").style.display = "block";
            document.getElementById("proxy-id").value = proxy.id;
            document.getElementById("proxy-created").textContent = proxy.created_at || "-";
            document.getElementById("proxy-updated").textContent = proxy.updated_at || proxy.created_at || "-";
            document.getElementById("proxy-bound").textContent = data.bound_profiles.map(p => p.name + "（" + p.id + "）").join("、") || "未绑定";
            document.getElementById("proxy-links-label").textContent = "当前节点链接（可复制或替换，ID 保持不变）";
            originalProxyLink = proxy.link;
            document.getElementById("proxy-links").value = originalProxyLink;
            document.getElementById("proxy-in-name").value = proxy.name;
            proxyNameBeforeEdit = proxy.name;
            const protocolSelect=document.getElementById("proxy-in-proto");
            const protocol=proxy.protocol==='socks5h'?'socks5':proxy.protocol;
            if ([...protocolSelect.options].some(option=>option.value===protocol)) protocolSelect.value=protocol;
            else {
                const keep=new Option('保留原协议','');keep.disabled=true;keep.dataset.legacy='1';
                protocolSelect.appendChild(keep);protocolSelect.value='';protocolSelect.dataset.savedProtocol=protocol;
            }
            document.getElementById("proxy-in-host").value = proxy.host;
            document.getElementById("proxy-in-port").value = proxy.port;
            document.getElementById("proxy-in-user").value = proxy.username || "";
            document.getElementById("proxy-in-pass").placeholder = "留空保持原密码";
            document.getElementById("proxy-password-actions").style.display = "block";
            document.getElementById("proxy-password-status").textContent = proxy.has_password ? "已保存密码；留空不修改" : "未保存密码";
            document.getElementById("proxy-password-reveal").textContent = "查看已保存密码";
            document.getElementById("proxy-password-reveal").disabled = !proxy.has_password;
            document.getElementById("proxy-save-button").textContent = "保存修改";
            document.getElementById("proxy-in-name").focus();
        }
        document.getElementById("proxy-in-name").addEventListener("input", event => {
            if (!currentEditingProxyId) return;
            const link = document.getElementById("proxy-links");
            link.value = ProxyEditor.syncName(link.value, proxyNameBeforeEdit, event.target.value);
            proxyNameBeforeEdit = event.target.value.trim();
        });
        async function showProxyPassword() {
            const input = document.getElementById("proxy-in-pass");
            const button = document.getElementById("proxy-password-reveal");
            if (input.type === "text") { input.type = "password"; button.textContent = "显示密码"; return; }
            if (!input.value && currentEditingProxyId) {
                const id = currentEditingProxyId;
                const data = await (await fetch("/__suite/api/proxies/detail?id=" + encodeURIComponent(id) + "&reveal_password=1")).json();
                if (currentEditingProxyId !== id) return;
                input.value = data.proxy.password || "";
            }
            input.type = "text"; button.textContent = "隐藏密码";
        }
        function openCreateModal() {
            PortalUI.open("modal-create-profile");
        }
        function openOAuthModal() {
            PortalUI.open("modal-oauth");
            document.getElementById("oauth-step-2").style.display = "none";
            document.getElementById("btn-oauth-open").style.display = "block";
        }
        function closeModals() {
            cancelAuthenticatorSetup().catch(() => {});
            clearRecoveryDisplay();
            document.getElementById("settings-current").value = "";
            document.getElementById("settings-new").value = "";
            document.getElementById("mfa-current-code").value = "";
            document.getElementById("external-import-json").value = "";
            PortalUI.closeAll();
            currentEditingProxyId = null;
            originalProxyLink = "";
            proxyNameBeforeEdit = "";
            document.getElementById("proxy-links").value = "";
            document.getElementById("proxy-in-pass").value = "";
            document.getElementById("proxy-in-pass").type = "password";
        }

        // Actions
        async function saveProxy() {
            const links = document.getElementById("proxy-links").value.trim();
            if (currentEditingProxyId) {
                const fields = {name:document.getElementById("proxy-in-name").value,
                        protocol:document.getElementById("proxy-in-proto").value || document.getElementById("proxy-in-proto").dataset.savedProtocol,
                        host:document.getElementById("proxy-in-host").value,
                        port:document.getElementById("proxy-in-port").value,
                        username:document.getElementById("proxy-in-user").value,
                        password:document.getElementById("proxy-in-pass").value,
                        clear_password:document.getElementById("proxy-clear-password").checked};
                const data = ProxyEditor.buildUpdate(currentEditingProxyId, originalProxyLink, links, fields);
                const button = document.getElementById("proxy-save-button");
                PortalUI.clearError("modal-proxy");
                button.disabled = true;
                try {
                    await fetch("/__suite/api/proxies/update", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});
                    closeModals(); await fetchStatus();
                } catch (error) { PortalUI.showError("modal-proxy", error.message); }
                finally { button.disabled = false; }
                return;
            }
            const name = document.getElementById("proxy-in-name").value;
            const proto = document.getElementById("proxy-in-proto").value;
            const host = document.getElementById("proxy-in-host").value;
            const port = document.getElementById("proxy-in-port").value;
            const username = document.getElementById("proxy-in-user").value;
            const password = document.getElementById("proxy-in-pass").value;
            if (links) {
                const manual = {};
                for (const [key,value] of Object.entries({name,host,port,username,password})) {
                    if (value !== "") manual[key] = value;
                }
                try {
                    if (Object.keys(manual).length) {
                        manual.protocol = proto;
                        await fetch("/__suite/api/proxies", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({...manual,link:links})});
                    } else {
                        await fetch("/__suite/api/proxies/import-links", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({links})});
                    }
                    closeModals(); await fetchStatus();
                } catch (error) { alert(error.message); }
                return;
            }

            if (!name || !host || !port) {
                alert("请完整填写节点名称、主机和端口！");
                return;
            }

            await fetch("/__suite/api/proxies", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({ name, protocol: proto, host, port, username, password })
            });
            closeModals();
            fetchStatus();
        }

        async function testProxy(id) {
            const btn = document.getElementById(`test-btn-${id}`);
            const resEl = document.getElementById(`test-result-${id}`);
            btn.disabled = true;
            resEl.textContent = "正在测试...";
            try {
                const res = await fetch("/__suite/api/proxies/test", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({ proxy_id: id })
                });
                const data = await res.json();
                if (data.ok) {
                    resEl.innerHTML = `<span style="color:#34d399;">✓ 出口 IP: <b>${escapeHtml(data.ip)}</b> (${data.latency_ms}ms)</span>`;
                } else {
                    resEl.innerHTML = `<span style="color:#f87171;">✗ 失败: ${escapeHtml(data.error)}</span>`;
                }
            } catch (e) {
                resEl.textContent = "请求异常";
            } finally {
                btn.disabled = false;
            }
        }

        async function deleteProxy(id) {
            if (!confirm("确定删除该代理节点吗？")) return;
            await fetch(`/__suite/api/proxies?id=${id}`, { method: "DELETE" });
            fetchStatus();
        }

        async function saveNewProfile() {
            const name = document.getElementById("prof-in-name").value;
            const proxy_id = document.getElementById("prof-in-proxy").value;
            if (!name) { alert("请填写配置名称"); return; }
            await fetch("/__suite/api/profiles", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({ name, proxy_id })
            });
            closeModals();
            fetchStatus();
        }

        function profileAction(id) {
            if (currentStatus.active_profile?.id === id && currentStatus.antigravity_ready && !currentStatus.starting_profile_id) {
                window.location.href = "/";
                return;
            }
            return switchProfile(id);
        }
        async function switchProfile(id) {
            if (!coreStatus?.installed) { openCoreInstallModal(); return; }
            localStartingId = id; renderUI();
            try {
                await fetch("/__suite/api/profiles/switch", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({profile_id:id})});
                localStartingId = null;
                await fetchStatus();
            } catch (error) {
                localStartingId = null; await fetchStatus();
                alert("可能是启动任务未提交或网络异常：" + error.message);
            }
        }

        async function openCoreInstallModal() {
            await fetch('/__suite/api/system/discover', {method:'POST'});
            await fetchCoreStatus();
            PortalUI.open("modal-core-install");
            document.getElementById("btn-uninstall-core").style.display = coreStatus?.installed ? 'inline-flex' : 'none';
            document.getElementById("btn-do-core-install").style.display = coreStatus?.installed ? 'none' : 'inline-flex';
            document.getElementById("btn-do-core-install").textContent = "安装反重力";
            const box = document.getElementById("core-install-progress-box"); box.style.display = "block";
            document.getElementById("core-install-status-text").textContent = coreStatus?.installed ?
                "已发现可用反重力：" + coreStatus.binary_path + "。项目使用现有本体和配置；卸载会保留账号、配置及聊天记录。" : "未发现可用本体，请安装反重力。已有配置会保留。";
            const task = coreStatus?.install_state?.status;
            if (['queued','downloading','installing','uninstalling'].includes(task)) pollCoreTask();
        }
        function pollCoreTask() {
            if (coreInstallPollTimer) clearInterval(coreInstallPollTimer);
            const install = document.getElementById("btn-do-core-install");
            const uninstall = document.getElementById("btn-uninstall-core");
            install.disabled = uninstall.disabled = true;
            document.getElementById("core-install-progress-box").style.display = "block";
            const poll = async () => {
                try {
                    const data = await (await fetch("/__suite/api/system/install-core/status")).json();
                    const st = data.state;
                    document.getElementById("core-install-status-text").textContent = st.status === 'error' ? '异常：可能是安装环境、下载或权限问题。' + st.message : st.message;
                    if (['done','error','idle'].includes(st.status)) {
                        clearInterval(coreInstallPollTimer); coreInstallPollTimer = null;
                        await fetchStatus(); install.disabled = false; uninstall.disabled = !coreStatus?.installed;
                        install.style.display = coreStatus?.installed ? 'none' : 'inline-flex';
                        uninstall.style.display = coreStatus?.installed ? 'inline-flex' : 'none';
                        install.textContent = '安装反重力';
                    }
                } catch (error) { clearInterval(coreInstallPollTimer); install.disabled = false; alert('可能是服务连接异常：' + error.message); }
            };
            coreInstallPollTimer = setInterval(poll, 1500); poll();
        }
        async function startCoreInstall() {
            await fetch("/__suite/api/system/install-core", {method:"POST"}); pollCoreTask();
        }
        async function uninstallCore() {
            if (!confirm("卸载当前发现的反重力本体？会先停止对应实例，桌面和网页暂时都不能使用。原配置、账号和聊天记录保留，本体留存备份。")) return;
            await fetch("/__suite/api/system/uninstall-core", {method:"POST"}); pollCoreTask();
        }

        async function openEditModal(id) {
            currentEditingProfileId = id;
            const m = document.getElementById("modal-edit-profile");
            PortalUI.open("modal-edit-profile");

            document.getElementById("edit-prof-id").value = id;
            document.getElementById("edit-prof-name").value = "加载中...";
            document.getElementById("edit-prof-email").value = "";
            document.getElementById("edit-prof-created").textContent = "-";
            document.getElementById("edit-prof-used").textContent = "-";
            document.getElementById("edit-prof-files").innerHTML = "正在读取配置文件...";

            try {
                const res = await fetch(`/__suite/api/profiles/detail?id=${id}`);
                const data = await res.json();
                if (data.ok) {
                    const p = data.profile;
                    document.getElementById("edit-prof-name").value = p.name || "";
                    document.getElementById("edit-prof-email").value = (p.email && p.email !== "未登录") ? p.email : "";
                    document.getElementById("edit-prof-proxy").value = p.proxy_id || "";
                    document.getElementById("edit-prof-created").textContent = p.created_at || "-";
                    document.getElementById("edit-prof-used").textContent = p.last_used_at || "-";

                    const fileList = data.files || [];
                    if (fileList.length === 0) {
                        document.getElementById("edit-prof-files").innerHTML = `<span style="color:var(--text-sub);">暂无持久化凭据文件（尚未运行或空档案）</span>`;
                    } else {
                        let html = "";
                        fileList.forEach((f, index) => {
                            const sz = (f.size / 1024).toFixed(1) + " KB";
                            html += `<div><button class="btn btn-secondary" data-file-index="${index}">编辑</button> ${escapeHtml(f.path)} <span style="color:var(--text-sub);">(${sz})</span></div>`;
                        });
                        document.getElementById("edit-prof-files").innerHTML = html;
                        document.querySelectorAll("[data-file-index]").forEach(button => {
                            button.onclick = () => editProfileFile(id, fileList[Number(button.dataset.fileIndex)].path);
                        });
                    }
                } else {
                    alert("加载档案详情失败: " + data.error);
                }
            } catch(e) {
                alert("网络请求异常: " + e.message);
            }
        }

        async function saveProfileEdit() {
            if (!currentEditingProfileId) return;
            const name = document.getElementById("edit-prof-name").value.trim();
            const email = document.getElementById("edit-prof-email").value.trim();
            const proxy_id = document.getElementById("edit-prof-proxy").value;

            if (!name) {
                alert("档案名称不能为空！");
                return;
            }

            try {
                const res = await fetch("/__suite/api/profiles/update", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({
                        id: currentEditingProfileId,
                        name: name,
                        email: email,
                        proxy_id: proxy_id
                    })
                });
                const data = await res.json();
                if (data.ok) {
                    alert("✅ 档案信息已成功更新！");
                    closeModals();
                    fetchStatus();
                } else {
                    alert("保存失败: " + data.error);
                }
            } catch(e) {
                alert("请求异常: " + e.message);
            }
        }

        async function toggleDefault(id) {
            const isCurrentlyDefault = currentStatus.default_profile_id === id;
            const targetId = isCurrentlyDefault ? null : id;
            await fetch("/__suite/api/profiles/set-default", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({ profile_id: targetId })
            });
            fetchStatus();
        }

        async function deleteProfile(id) {
            if (!confirm("确定删除该配置档案吗？该操作不可逆！")) return;
            await fetch(`/__suite/api/profiles?id=${id}`, { method: "DELETE" });
            fetchStatus();
        }

        function showTransferStatus(message,options) { SuiteTransfer.show(message,options); }
        function exportProfile(id) {
            const profile = currentStatus.profiles.find(p => p.id === id);
            const proxy = currentStatus.proxies.find(p => p.id === profile.proxy_id);
            exportProfileId = id;
            document.getElementById("export-profile-name").textContent = profile.name;
            document.getElementById("export-proxy-option").style.display = "block";
            document.getElementById("export-include-proxy").checked = false;
            document.getElementById("export-include-proxy").disabled = !proxy;
            document.getElementById("export-include-history").checked = false;
            document.getElementById("export-proxy-detail").textContent = proxy ? proxy.name + " · " + proxy.protocol + "://" + proxy.host + ":" + proxy.port : "无对应代理";
            chooseExportFormat('suite');
            PortalUI.open("modal-export-profile");
        }
        function chooseExportFormat(format) {
            selectedExportFormat = format;
            for (const name of ['suite','cpa','sub2api']) {
                document.getElementById('export-format-' + name).className = name === format ? 'btn btn-primary' : 'btn btn-secondary';
            }
            document.getElementById('export-native-proxy').style.display = format === 'suite' ? 'block' : 'none';
            updateExportDescription();
        }
        function updateExportDescription() {
            const format=selectedExportFormat;
            const history=format==='suite'&&document.getElementById('export-include-history').checked;
            const proxy=format==='suite'&&document.getElementById('export-include-proxy').checked;
            document.getElementById('export-format-description').textContent=format==='suite'?
                '本次导出：账号配置'+(history?' + 本地全部聊天记录':'，不含聊天记录')+(proxy?' + 对应代理':'，不含代理')+'。':
                (format==='cpa'?'CPA 账号 JSON':'Sub2API 账号数据 JSON')+'：只导出 Antigravity 登录凭据，不含代理和聊天记录。';
            document.getElementById('export-private-warning').textContent='导出含账号登录凭据'+(history?'、全部本地聊天记录':'')+(proxy?'、节点密码':'')+'，请私有保管。';
        }
        async function downloadProfile() {
            if (!exportProfileId) return;
            const button = document.getElementById("export-profile-button");
            button.disabled = true; button.textContent = "导出中…";
            try {
                const id = exportProfileId;
                const format = selectedExportFormat;
                const includeProxy = format === 'suite' && document.getElementById("export-include-proxy").checked;
                const includeHistory = format === 'suite' && document.getElementById("export-include-history").checked;
                const response = await fetch("/__suite/api/profiles/export?id=" + encodeURIComponent(id) + "&format=" + format + "&include_proxy=" + (includeProxy ? "1" : "0")+"&include_history="+(includeHistory?'1':'0'));
                const url = URL.createObjectURL(await response.blob());
                const link = document.createElement("a"); link.href = url; link.download = format + "_" + id + (format === 'suite' ? '.tar.gz' : '.json');
                document.body.appendChild(link); link.click(); link.remove();
                setTimeout(() => URL.revokeObjectURL(url), 30000);
                closeModals(); showTransferStatus(format !== 'suite' ? '账号已导出为 ' + (format === 'cpa' ? 'CPA' : 'Sub2API') + ' 格式，不含代理和聊天记录。' : '配置已导出，'+(includeHistory?'包含本地全部聊天记录':'不含聊天记录')+'，'+(includeProxy?'包含对应代理':'不含代理')+'。');
            } catch (error) { alert(error.message); }
            finally { button.disabled = false; button.textContent = "导出"; }
        }
        function openImportSources() {
            PortalUI.open("modal-import-sources");
        }
        function chooseImportSource(source) {
            selectedImportSource = source;
            PortalUI.close("modal-import-sources");
            document.getElementById("import-file-input").accept = source === 'suite' ? '.gz,.tar,.agp' : '.json,application/json';
            if (source === 'suite') { document.getElementById("import-file-input").click(); return; }
            document.getElementById("external-import-title").textContent = source === 'cpa' ? '从 CPA 导入' : '从 Sub2API 导入';
            document.getElementById("external-import-help").textContent = source === 'cpa' ?
                '选择 CPA 导出的 Antigravity 账号 JSON（type 为 antigravity）。支持单个账号或账号数组，过期凭据会在启动检测时尝试续期。' :
                '选择 Sub2API 导出的账号数据 JSON（包含 accounts）。只导入其中 Antigravity OAuth 账号；代理信息忽略，导入后由你自己配。';
            document.getElementById("external-import-json").value = "";
            PortalUI.open("modal-external-import");
        }
        async function importPastedJson() {
            const value = document.getElementById("external-import-json").value.trim();
            if (!value) { alert('请粘贴导出的 JSON'); return; }
            const button = document.getElementById("external-json-button");button.disabled = true;
            try { await previewImportFile(new File([value], selectedImportSource + '.json', {type:'application/json'}), selectedImportSource); }
            finally { button.disabled = false; }
        }
        async function handleImport(event) {
            const file = event.target.files[0];
            if (!file) return;
            event.target.value = "";
            await previewImportFile(file, selectedImportSource);
        }
        async function previewImportFile(file, source) {
            if(importingProfile)return;
            importingProfile=true;SuiteTransfer.begin('正在上传配置包');
            try {
                if (pendingProfileImport) await cancelProfileImport();
                const preview = await SuiteTransfer.upload(file,source);
                pendingProfileImport = preview;
                document.getElementById("external-import-json").value = "";
                PortalUI.close("modal-external-import");
                if (!preview.has_proxy && !preview.has_history) {
                    SuiteTransfer.stage(source === 'suite' ? "无代理文件，正在保存配置" : "正在转换并保存账号，代理由你自己配置");
                    await finishProfileImport(false);
                    return;
                }
                document.getElementById("import-profile-name").textContent = preview.name;
                document.getElementById("import-profile-warnings").textContent = (preview.warnings || []).join('\n');
                document.getElementById("import-include-proxy").checked = false;
                document.getElementById("import-proxy-option").hidden = !preview.has_proxy;
                document.getElementById("import-history-option").hidden = !preview.has_history;
                document.getElementById("import-include-history").checked = false;
                document.querySelector('input[name="history-mode"][value="merge"]').checked=true;
                document.getElementById("import-history-detail").textContent=preview.has_history?'包含 '+preview.history_files+' 个聊天文件；默认不导入。':'无聊天文件';
                updateImportHistoryChoice();
                document.getElementById("import-proxy-detail").textContent = (preview.proxy_count > 1 ? '有 ' + preview.proxy_count + ' 个账号附带代理，选择后逐一绑定。示例：' : '') + (preview.has_proxy ? preview.proxy.name + " · " + preview.proxy.protocol + "://" + preview.proxy.host + ":" + preview.proxy.port : '无代理文件');
                PortalUI.open("modal-import-profile");
                showTransferStatus("请选择是否导入附带代理和聊天记录",{phase:'waiting',persistent:true});
            } catch (error) { SuiteTransfer.fail(error); }
            finally {importingProfile=false;}
        }
        function updateImportHistoryChoice(){
            const include=!!pendingProfileImport?.has_history&&document.getElementById('import-include-history').checked;
            document.getElementById('import-history-mode').hidden=!include;
            document.getElementById('import-history-warning').hidden=!include||document.querySelector('input[name="history-mode"]:checked').value!=='replace';
        }
        async function finishProfileImport(includeProxy,includeHistory=false,historyMode='merge',confirmReplace=false) {
            if (!pendingProfileImport) return;
            SuiteTransfer.stage(includeHistory?'正在保存配置并'+(historyMode==='replace'?'覆盖':'合并')+'聊天记录':'正在保存账号配置，聊天记录不变');
            const result = await (await fetch("/__suite/api/profiles/import/confirm", {method:"POST",headers:{"Content-Type":"application/json"},
                body:JSON.stringify({import_token:pendingProfileImport.import_token,include_proxy:includeProxy,include_history:includeHistory,history_mode:historyMode,confirm_replace:confirmReplace})})).json();
            pendingProfileImport = null;
            closeModals(); await fetchStatus();
            const count = result.imported_count || 1;
            showTransferStatus(result.proxy_message + "。" + (result.history_message||'') + "。已导入 " + count + " 个配置" + (result.warnings ? '，已安排自动检测；详细结果见各配置卡片。' : ''));
        }
        async function confirmProfileImport() {
            if(importingProfile)return;
            const includeHistory=!!pendingProfileImport?.has_history&&document.getElementById('import-include-history').checked;
            const mode=document.querySelector('input[name="history-mode"]:checked').value;
            if(includeHistory&&mode==='replace'&&!confirm('覆盖会替换本机所有账号共用的全部聊天记录，旧记录不会因切换配置而恢复。确定覆盖导入吗？'))return;
            importingProfile=true;
            const button = document.getElementById("import-profile-button");
            button.disabled = true; button.textContent = "导入中…";
            try { await finishProfileImport(document.getElementById("import-include-proxy").checked,includeHistory,mode,includeHistory&&mode==='replace'); }
            catch (error) { SuiteTransfer.fail(error); }
            finally {importingProfile=false; button.disabled = false; button.textContent = "导入"; }
        }
        async function cancelProfileImport() {
            if (pendingProfileImport) {
                await fetch("/__suite/api/profiles/import/cancel", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({import_token:pendingProfileImport.import_token})});
                pendingProfileImport = null;
            }
            PortalUI.close("modal-import-profile");
            if(!importingProfile)showTransferStatus('已取消导入');
        }

        // OAuth Flow (like CPA)
        let oauthSessionState = null;
        async function startOAuthFlow() {
            const res = await fetch("/__suite/api/auth/google/start", { method: "POST" });
            const data = await res.json();
            oauthSessionState = data.state;
            window.open(data.auth_url, "_blank");
            document.getElementById("btn-oauth-open").style.display = "none";
            document.getElementById("oauth-step-2").style.display = "block";
        }

        async function completeOAuth() {
            let code = document.getElementById("oauth-code-input").value.trim();
            const name = document.getElementById("oauth-prof-name").value.trim();
            const proxy_id = document.getElementById("oauth-prof-proxy").value;
            if (!code) { alert("请输入授权网址或授权码！"); return; }
            if (!name) { alert("请为该档案命名！"); return; }

            const res = await fetch("/__suite/api/auth/google/complete", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    code: code,
                    state: oauthSessionState,
                    name: name,
                    proxy_id: proxy_id
                })
            });
            const result = await res.json();
            if (result.ok) {
                closeModals();
                await fetchStatus();
                alert("授权凭据已保存，已自动检测一次。请查看该档案的检测结果；需要使用时点击“切换并启动”。");
            } else {
                alert("登录失败: " + result.error);
            }
        }

        async function logout() {
            await fetch("/__suite/api/logout", {method:"POST"});
            window.location.href = "/";
        }

        fetchStatus();
        setInterval(fetchStatus, 2000);
        document.addEventListener('visibilitychange',()=>{if(!document.hidden)fetchStatus();});
