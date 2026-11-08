/* Keep the uninitialized native shell covered until its first state and history arrive. */
(()=>{
 let state=false,history=false,auth=false,error=false,overlay=null,timer=null,started=Date.now();
 function render(){
  if(!overlay||(!state&&!history&&!document.body))return;
  const message=overlay.querySelector('[data-message]');
  message.textContent=error?'原生连接尚未完成，可能是反代 WebSocket 或引擎连接异常。聊天记录仍保存在本机。':
   !state?'正在连接反重力并初始化操作…':!history?'正在读取本地聊天列表，记录尚未显示，请稍候…':!auth?'正在等待账号状态，页面操作尚未准备完成…':'初始化完成';
  overlay.querySelector('[data-time]').textContent='已用 '+Math.floor((Date.now()-started)/1000)+' 秒';
 }
 function install(){
  if(overlay)return;
  overlay=document.createElement('div');overlay.id='agy-ide-initializing';overlay.setAttribute('role','status');
  overlay.style.cssText='position:fixed;inset:0;z-index:2147483645;background:#0f172af2;display:flex;align-items:center;justify-content:center;color:#e2e8f0;font:15px system-ui';
  overlay.innerHTML='<div style="max-width:480px;padding:30px;text-align:center"><strong style="font-size:20px">反重力正在准备</strong><p data-message style="line-height:1.7;margin:20px 0"></p><p data-time></p><p style="font-size:13px;color:#94a3b8">初始化完成后，聊天列表和页面操作会一起可用。</p><a href="/?portal=1" style="display:inline-block;padding:12px 18px;color:#38bdf8">返回配置中心</a></div>';
  document.body.append(overlay);timer=setInterval(render,1000);render();
  if(state&&history&&auth){clearInterval(timer);overlay.remove();}
 }
 window.addEventListener('agy-native-stream',event=>{
  if(event.detail.error)error=true;
  else{
   if(event.detail.method==='JetboxSubscribeToState')state=true;
   if(event.detail.method==='JetboxSubscribeToSummaries')history=true;
   if(event.detail.method==='GetAuthStatus')auth=true;
  }
  render();
  if(state&&history&&auth){clearInterval(timer);requestAnimationFrame(()=>requestAnimationFrame(()=>overlay?.remove()));}
 });
 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
})();
