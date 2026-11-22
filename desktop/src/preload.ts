import {contextBridge,ipcRenderer} from 'electron';
contextBridge.exposeInMainWorld('horizon',{send:(command:unknown)=>ipcRenderer.send('command',command),subscribe:(callback:(event:any)=>void)=>{const listener=(_:unknown,e:unknown)=>callback(e);ipcRenderer.on('event',listener);return()=>ipcRenderer.removeListener('event',listener);}});

if (process.platform === 'win32') {
  window.addEventListener('DOMContentLoaded', () => {
    document.documentElement.classList.add('windows-caption');
  }, {once: true});
}
