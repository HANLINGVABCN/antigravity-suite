const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('antigravitySuiteNative', {
    request:(operation,fields={})=>ipcRenderer.invoke('agy-suite:control',operation,fields),
    warn:()=>ipcRenderer.invoke('agy-suite:warning'),
    openPanel:()=>ipcRenderer.invoke('agy-suite:open-panel')
});
