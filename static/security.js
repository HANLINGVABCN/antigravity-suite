/* Optional authenticator setup; no third-party QR or analytics endpoints. */
let authenticatorSetupToken = null;
let authenticatorRequestPending = false;

async function securityRequest(operation, fields = {}) {
    if (authenticatorRequestPending) throw new Error('验证操作正在进行，请稍候');
    authenticatorRequestPending = true;
    try {
        const response = await fetch('/__suite/api/system/security/' + operation, {
            method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(fields)
        });
        const data = await response.json();
        if (!data.ok) throw new Error(data.error || '验证操作失败');
        return data;
    } finally { authenticatorRequestPending = false; }
}
function securityCredentials() {
    return {current_password:document.getElementById('settings-current').value,
            code:document.getElementById('mfa-current-code').value.trim()};
}
async function loadAuthenticatorStatus() {
    const result = await (await fetch('/__suite/api/system/security/status')).json();
    document.getElementById('mfa-status').textContent = result.enabled ?
        '已开启 · 剩余恢复码 ' + result.recovery_codes_remaining + ' 条' : '未开启 · 当前登录只需管理员密码';
    document.getElementById('mfa-status').style.color = result.enabled ? '#6ee7b7' : '#94a3b8';
    document.getElementById('mfa-disabled-actions').style.display = result.enabled ? 'none' : 'block';
    document.getElementById('mfa-enabled-actions').style.display = result.enabled ? 'block' : 'none';
}
async function beginAuthenticatorSetup() {
    try {
        const data = await securityRequest('setup', securityCredentials());
        authenticatorSetupToken = data.setup_token;
        clearRecoveryDisplay();
        document.getElementById('mfa-setup-secret').value = data.secret;
        document.getElementById('mfa-setup-code').value = '';
        document.getElementById('mfa-qr').src = '/__suite/api/system/security/qr?setup_token=' + encodeURIComponent(data.setup_token);
        document.getElementById('mfa-setup').style.display = 'block';
    } catch (error) { alert(error.message); }
}
async function cancelAuthenticatorSetup() {
    const token = authenticatorSetupToken;
    authenticatorSetupToken = null;
    document.getElementById('mfa-setup').style.display = 'none';
    document.getElementById('mfa-setup-secret').value = '';
    document.getElementById('mfa-setup-code').value = '';
    document.getElementById('mfa-qr').removeAttribute('src');
    if (token) {
        await fetch('/__suite/api/system/security/cancel', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({setup_token:token})});
    }
}
function clearRecoveryDisplay() {
    document.getElementById('mfa-recovery-codes').value = '';
    document.getElementById('mfa-recovery').style.display = 'none';
}
function showRecoveryCodes(codes) {
    document.getElementById('mfa-recovery-codes').value = codes.join('\n');
    document.getElementById('mfa-recovery').style.display = 'block';
}
async function enableAuthenticator() {
    try {
        const data = await securityRequest('enable', {...securityCredentials(),setup_token:authenticatorSetupToken,
            code:document.getElementById('mfa-setup-code').value.trim()});
        authenticatorSetupToken = null;
        await cancelAuthenticatorSetup();
        await loadAuthenticatorStatus(); showRecoveryCodes(data.recovery_codes);
        document.getElementById('settings-current').value = '';
        alert('验证器已开启，其他登录会话已失效。请保存恢复码。');
    } catch (error) { alert(error.message); }
}
async function disableAuthenticator() {
    if (!confirm('关闭验证器两步验证？之后登录只需密码，旧恢复码将失效。')) return;
    try {
        await securityRequest('disable', securityCredentials());
        clearRecoveryDisplay(); await loadAuthenticatorStatus();
        document.getElementById('settings-current').value = '';
        document.getElementById('mfa-current-code').value = '';
        alert('验证器已关闭，其他登录会话已失效。');
    } catch (error) { alert(error.message); }
}
async function regenerateRecoveryCodes() {
    if (!confirm('生成新的恢复码？旧恢复码将全部失效。')) return;
    try {
        const data = await securityRequest('recovery', securityCredentials());
        await loadAuthenticatorStatus(); showRecoveryCodes(data.recovery_codes);
        document.getElementById('settings-current').value = '';
        document.getElementById('mfa-current-code').value = '';
    } catch (error) { alert(error.message); }
}
function downloadRecoveryCodes() {
    const codes = document.getElementById('mfa-recovery-codes').value;
    if (!codes) return;
    const text = 'Antigravity Suite 恢复码\n每条仅能使用一次；登录仍需要管理员密码。请私有保存。\n\n' + codes + '\n';
    const url = URL.createObjectURL(new Blob([text],{type:'text/plain;charset=utf-8'}));
    const link = document.createElement('a'); link.href = url; link.download = 'antigravity-recovery-codes.txt';
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url),30000);
}
