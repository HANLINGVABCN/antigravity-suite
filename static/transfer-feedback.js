/* Import feedback stays visible while upload and server processing are pending. */
(() => {
    let started=0,label='',ticker=null,hideTimer=null;
    function element(){return document.getElementById('transfer-status');}
    function draw(phase,message){
        const status=element();if(!status)return;
        status.style.display='block';status.dataset.phase=phase;
        status.setAttribute('aria-busy',String(phase==='working'));
        status.style.borderColor=phase==='error'?'#ef4444':phase==='success'?'#34d399':'#38bdf8';
        status.style.background=phase==='error'?'#451a1a':'#1e293b';
        status.textContent=message;
    }
    function tick(){
        const seconds=Math.floor((Date.now()-started)/1000);
        draw('working',label+' · 已用 '+seconds+' 秒'+(seconds>=30?'。仍在处理，请勿重复导入。':''));
    }
    function stop(){clearInterval(ticker);clearTimeout(hideTimer);ticker=null;hideTimer=null;}
    window.SuiteTransfer={
        begin(message){stop();started=Date.now();label=message;tick();ticker=setInterval(tick,1000);},
        stage(message){label=message;if(!ticker)this.begin(message);else tick();},
        show(message,{phase='success',persistent=false}={}){
            stop();draw(phase,message);
            if(!persistent && phase!=='error')hideTimer=setTimeout(()=>{element().style.display='none';},15000);
        },
        fail(error){this.show('导入未完成：'+(error?.message||'可能是连接中断，请检查配置列表后重试'),{phase:'error',persistent:true});},
        upload(file,source='suite'){
            return new Promise((resolve,reject)=>{
                const xhr=new XMLHttpRequest();xhr.open('POST','/__suite/api/profiles/import/preview?source='+encodeURIComponent(source));
                xhr.timeout=300000;
                xhr.upload.onprogress=event=>{
                    this.stage(event.lengthComputable?'正在上传配置包 '+Math.round(event.loaded/event.total*100)+'%':'正在上传配置包');
                };
                xhr.upload.onload=()=>this.stage('上传完成，正在检查配置包');
                xhr.onload=()=>{
                    let data;try{data=JSON.parse(xhr.responseText);}catch{reject(new Error('可能是反代返回异常或服务连接中断，请刷新配置列表确认后重试'));return;}
                    if(xhr.status===401){window.location.href='/';reject(new Error('后台登录已失效，请重新登录'));return;}
                    if(xhr.status<200||xhr.status>=300||!data.ok){reject(new Error(data.error||'检查配置包失败（HTTP '+xhr.status+'）'));return;}
                    resolve(data);
                };
                xhr.onerror=()=>reject(new Error('可能是上传连接或反代中断，请检查网络后重试'));
                xhr.ontimeout=()=>reject(new Error('上传或检查超过 5 分钟，可能是网络或服务器处理缓慢，请刷新配置列表确认后重试'));
                const form=new FormData();form.append('file',file);xhr.send(form);
            });
        }
    };
})();
