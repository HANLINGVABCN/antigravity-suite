/* Return to the panel when the observed native process stops; do not relaunch it. */
(()=>{
 let pending=false;
 async function check(){
  if(pending)return;pending=true;
  try{
   const response=await fetch('/__suite/api/status',{cache:'no-store'});
   if(response.status===401){location.replace('/?portal=1');return;}
   if(!response.ok)return;
   const state=await response.json();
   if(state.engine_running===false&&!state.busy&&!state.starting_profile_id&&!state.opening_login)location.replace('/?portal=1');
  }catch{/* A temporary network disconnect is not proof that the process closed. */}
  finally{pending=false;}
 }
 setInterval(check,2000);
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)check();});
 check();
})();
