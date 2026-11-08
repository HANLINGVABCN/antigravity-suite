/* Native subscriptions use WebSockets instead of exhausting HTTP/1.1 connections. */
(()=>{
 if(window.__AGY_STREAM_TRANSPORT__)return;window.__AGY_STREAM_TRANSPORT__=true;
 const methods=new Set(['JetboxSubscribeToState','JetboxSubscribeToSummaries','ProjectUpdatesStream','SubscribeToSidecars','SubscribeToMcpServerStates','SubscribeToPlugins']);
 const prefix='/exa.language_server_pb.LanguageServerService/',original=window.fetch.bind(window);
 const signal=(method,error=false)=>window.dispatchEvent(new CustomEvent('agy-native-stream',{detail:{method,error}}));
 window.fetch=async function(input,options){
  let url;try{url=new URL(typeof input==='string'||input instanceof URL?input:input.url,location.href)}catch{return original(input,options)}
  const method=url.pathname.startsWith(prefix)?url.pathname.slice(prefix.length):'';
  if(url.origin!==location.origin||!methods.has(method)){
   const response=await original(input,options);
   if(url.origin===location.origin&&url.pathname===prefix+'GetAuthStatus'&&response.ok)signal('GetAuthStatus');
   if(url.origin===location.origin&&url.pathname===prefix+'HasAuthToken'&&response.ok){
    try{if(!(await response.clone().json()).hasToken)signal('GetAuthStatus')}catch{}
   }
   return response;
  }
  const request=new Request(input,options);if(request.method!=='POST')return original(input,options);
  if(request.signal.aborted)throw new DOMException('Aborted','AbortError');
  const bytes=new Uint8Array(await request.arrayBuffer());if(bytes.length>1024*1024)throw new Error('订阅请求过大');
  let encoded='';for(let i=0;i<bytes.length;i+=8192)encoded+=String.fromCharCode(...bytes.subarray(i,i+8192));
  const headers=Object.fromEntries(request.headers.entries());
  return new Promise((resolve,reject)=>{
   const socket=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/__suite/native-stream');socket.binaryType='arraybuffer';
   let controller,started=false,ended=false,notified=false;
   const openTimeout=setTimeout(()=>failed(new Error('可能是反代没有启用 WebSocket，订阅连接未能打开')),15000);
   const stream=new ReadableStream({start(c){controller=c},cancel(){ended=true;socket.close();request.signal.removeEventListener('abort',abort)}},{highWaterMark:1024*1024,size:chunk=>chunk.byteLength});
   function failed(error){if(ended)return;ended=true;clearTimeout(openTimeout);signal(method,true);controller.error(error);if(!started)reject(error);socket.close();request.signal.removeEventListener('abort',abort)}
   function abort(){failed(new DOMException('Aborted','AbortError'))}
   request.signal.addEventListener('abort',abort,{once:true});
   socket.onopen=()=>{clearTimeout(openTimeout);socket.send(JSON.stringify({method:'POST',subscription:method,headers,body:btoa(encoded)}))};
   socket.onmessage=event=>{
    if(ended)return;
    if(typeof event.data==='string'){
     let data;try{data=JSON.parse(event.data)}catch{failed(new Error('原生订阅响应异常'));return}
     if(data.error){failed(new Error(data.error));return}
     if(data.end){ended=true;controller.close();socket.close();request.signal.removeEventListener('abort',abort);return}
     if(!started){started=true;resolve(new Response(stream,{status:data.status,headers:data.headers}))}
    }else{
     if(controller.desiredSize < -16*1024*1024){failed(new Error('订阅积压过大，请刷新重试'));return}
     controller.enqueue(new Uint8Array(event.data));if(!notified){notified=true;signal(method)}
    }
   };
   socket.onerror=()=>failed(new Error('可能是原生订阅或反代 WebSocket 连接失败'));
   socket.onclose=()=>{if(!ended)failed(new Error('原生订阅连接已关闭'))};
  });
 };
})();
