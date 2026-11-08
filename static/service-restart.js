let managementRestartTimer=null,managementRestartJob=null,managementRestartInfo=null;
function managementRestartMessage(message){
    document.getElementById('service-restart-status').textContent=message;
    const header=document.getElementById('service-restart-header-status');
    header.textContent=message;header.hidden=!message;
}
function managementRestartButtons(busy,supported){
    for(const id of ['btn-service-restart','btn-service-restart-header']){
        const button=document.getElementById(id);button.disabled=busy||!supported;button.textContent=busy?'后台重启中…':'重启后台服务';
    }
}
async function loadServiceRestartStatus(){
    try {
        managementRestartInfo=await(await fetch('/__suite/api/system/service-restart/status')).json();
        const job=managementRestartInfo.job||{},busy=['queued','stopping','starting','recovering'].includes(job.phase);
        managementRestartButtons(busy,managementRestartInfo.supported);
        const status=document.getElementById('service-restart-status');
        status.textContent=managementRestartInfo.support_reason || (job.phase==='idle'?managementRestartInfo.port_pending?'当前端口 '+managementRestartInfo.running_port+'，已保存 '+managementRestartInfo.saved_port+'，重启后台后生效。':'当前后台端口 '+managementRestartInfo.running_port+'。':job.message);
        status.style.color=job.phase==='error'?'#f87171':job.phase==='success'?'#34d399':'#94a3b8';
        if(busy||managementRestartJob||job.phase==='error')managementRestartMessage(status.textContent);
        if(managementRestartJob===job.job_id && ['success','error'].includes(job.phase)){
            clearInterval(managementRestartTimer);managementRestartTimer=null;managementRestartJob=null;
            if(job.phase==='success')setTimeout(()=>location.reload(),1000);
        }
    }catch {
        if(managementRestartJob)managementRestartMessage('后台正在重启，页面等待连接恢复…');
    }
}
async function restartManagementService(fromSettings=true){
    if(managementRestartJob)return;
    if(!fromSettings)await loadServiceRestartStatus();
    const info=managementRestartInfo;
    if(!info?.supported)return;
    const requested=Number(document.getElementById('settings-port').value);
    if(fromSettings&&requested!==info.saved_port){PortalUI.showError('modal-service-settings','端口尚未保存，请先保存设置，再重启后台服务。');return;}
    PortalUI.clearError('modal-service-settings');managementRestartButtons(true,true);
    try{
        const data=await(await fetch('/__suite/api/system/service-restart',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})).json();
        managementRestartJob=data.job.job_id;managementRestartInfo=data;
        await loadServiceRestartStatus();
        managementRestartTimer=setInterval(loadServiceRestartStatus,1500);
        if(data.port_pending){
            const direct=location.port===String(data.running_port);
            const target=new URL(location.href);
            if(direct)target.port=String(data.saved_port);
            managementRestartMessage(direct?'后台重启后端口改为 '+data.saved_port+'，请用新地址重新打开。':'后台重启后请把反代或 SSH 转发目标改为 '+data.saved_port+'，然后刷新页面。');
            if(direct)for(const id of ['service-restart-status','service-restart-header-status']){const link=document.createElement('a');link.href=target.href;link.textContent='打开新地址';document.getElementById(id).append(' ',link);}
            clearInterval(managementRestartTimer);managementRestartTimer=null;
        }
    }catch(error){if(fromSettings)PortalUI.showError('modal-service-settings',error.message);managementRestartMessage(error.message);managementRestartButtons(false,info.supported);}
}
loadServiceRestartStatus();
