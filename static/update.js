let suiteUpdateTimer = null;
let suiteUpdateRequestedJob = null;
let suiteUpdateBusy = false;
const updatePhases = new Set(['queued','downloading','verifying','dependencies','backup','installing','restarting','rolling_back']);

function renderSuiteUpdate(data) {
    const job = data.job || {};
    suiteUpdateBusy = updatePhases.has(job.phase);
    document.getElementById('update-current-version').textContent = 'v' + data.current_version;
    document.getElementById('pill-suite-version').textContent = '项目 v' + data.current_version;
    document.getElementById('update-latest-version').textContent = data.latest_version ? 'v' + data.latest_version : '尚未检查';
    const status = document.getElementById('suite-update-status');
    status.className = 'diag ' + (job.phase === 'error' ? 'abnormal' : job.phase === 'success' ? 'normal' : suiteUpdateBusy ? 'checking' : '');
    status.textContent = suiteUpdateBusy || ['error','success'].includes(job.phase) ? job.message :
        data.check_error || (data.latest_version ? data.update_available ? '发现新版本，可以点击自动更新。' : '当前已是最新正式版。' : '点击检查更新，从本项目 GitHub 发布页读取版本。');
    if (job.phase === 'error' && data.check_error) status.textContent += ' ' + data.check_error;
    document.getElementById('suite-update-support').textContent = data.support_reason || '';
    document.getElementById('suite-update-notes-content').textContent = data.notes || '尚无更新说明。';
    document.getElementById('suite-check-update').disabled = suiteUpdateBusy;
    const button = document.getElementById('suite-start-update');
    button.disabled = suiteUpdateBusy || !data.supported || !data.update_available;
    button.textContent = suiteUpdateBusy ? '更新中…' : '自动更新';
    document.getElementById('btn-suite-update').textContent = suiteUpdateBusy ? '项目更新中…' : '更新项目';
    if (suiteUpdateBusy && !suiteUpdateTimer) suiteUpdateTimer = setInterval(pollSuiteUpdate, 1500);
    if (!suiteUpdateBusy && suiteUpdateTimer) { clearInterval(suiteUpdateTimer); suiteUpdateTimer = null; }
    if (job.phase === 'success' && suiteUpdateRequestedJob === job.job_id) {
        suiteUpdateRequestedJob = null;
        setTimeout(() => window.location.reload(), 1000);
    }
}

async function pollSuiteUpdate() {
    try {
        const response = await fetch('/__suite/api/system/update/status', {cache:'no-store'});
        renderSuiteUpdate(await response.json());
    } catch (error) {
        if (suiteUpdateBusy) {
            document.getElementById('suite-update-status').textContent = '后台正在重启或暂时无法连接，页面自动等待恢复…';
        } else {
            document.getElementById('suite-update-status').textContent = '可能是暂时连接失败，请稍后重新检查。';
        }
    }
}

async function openSuiteUpdate() {
    PortalUI.open('modal-suite-update');
    await pollSuiteUpdate();
    if (!suiteUpdateBusy) await checkSuiteUpdate();
}

async function checkSuiteUpdate() {
    const button = document.getElementById('suite-check-update');
    button.disabled = true;
    document.getElementById('suite-update-status').textContent = '正在检查 GitHub 最新正式版…';
    try {
        const response = await fetch('/__suite/api/system/update/check',{method:'POST'});
        renderSuiteUpdate(await response.json());
    } catch (error) {
        PortalUI.showError('modal-suite-update',error.message);
    } finally { button.disabled = suiteUpdateBusy; }
}

async function startSuiteUpdate() {
    const button = document.getElementById('suite-start-update');
    button.disabled = true;
    PortalUI.clearError('modal-suite-update');
    try {
        const response = await fetch('/__suite/api/system/update/start',{method:'POST'});
        const data = await response.json();
        suiteUpdateRequestedJob = data.job?.job_id || null;
        renderSuiteUpdate(data);
    } catch (error) {
        PortalUI.showError('modal-suite-update',error.message);
        await pollSuiteUpdate();
    }
}
pollSuiteUpdate();
