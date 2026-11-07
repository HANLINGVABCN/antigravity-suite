/* One engine button reflects stopped, starting and running states. */
(() => {
    let status=null,core=null,pending='',lastProfile=null;
    function render(nextStatus,nextCore){
        if(nextStatus)status=nextStatus;if(nextCore)core=nextCore;
        if(status?.active_profile_id)lastProfile=status.active_profile_id;
        const button=document.getElementById('btn-engine-toggle');if(!button)return;
        const starting=!!status?.starting_profile_id || !!status?.opening_login || (core?.running&&!status?.antigravity_ready);
        const running=!!status?.antigravity_ready || !!core?.running;
        button.disabled=!!pending || !!status?.busy || !!status?.starting_profile_id || (!core && !status) || core?.installed===false;
        button.textContent=pending==='stop'?'停止中…':pending==='start'||starting?'启动中…':running?'停止反重力':'启动反重力';
        if(starting)button.disabled=true;
    }
    async function toggle(){
        if(pending)return;
        const running=!!status?.antigravity_ready || !!core?.running;
        if(running && !window.confirm('停止反重力并保存当前配置？'))return;
        const target=status?.active_profile_id||status?.default_profile_id||lastProfile||(status?.profiles?.length===1?status.profiles[0].id:null);
        const empty=status?.native_session?.state==='logged_out' && status?.login_sessions?.waiting_for_login;
        if(!running&&!target&&!empty){window.alert('请先在配置列表选择需要启动的配置。');return;}
        pending=running?'stop':'start';render();
        try {
            await fetch(running?'/__suite/api/system/stop':empty?'/__suite/api/system/start':'/__suite/api/profiles/switch',{
                method:'POST',headers:{'Content-Type':'application/json'},body:running||empty?'{}':JSON.stringify({profile_id:target})
            });
            if(!running){status={...status,starting_profile_id:empty?null:target,opening_login:empty};render();}
            await window.fetchStatus();
        } catch(error){window.alert(error.message||'操作暂未完成，请刷新查看状态');}
        finally {pending='';render();}
    }
    window.SuiteCoreToggle={render,toggle};
})();
