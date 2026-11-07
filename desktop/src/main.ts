import {app,BrowserWindow,ipcMain} from 'electron';
import {join} from 'node:path';
import {createInterface} from 'node:readline';
import {connect} from 'node:net';
const bridge=connect({host:'127.0.0.1',port:Number(process.env.FRONTIER_DESKTOP_PORT)},()=>{
  bridge.write(process.env.FRONTIER_DESKTOP_TOKEN+'\n');
});
bridge.on('error',()=>app.quit());
let win: BrowserWindow; let ready=false; const pending:unknown[]=[];
const send=(event:unknown)=>{if(ready)win.webContents.send('event',event);else pending.push(event);};
createInterface({input:bridge}).on('line',line=>{try {const e=JSON.parse(line); if(e.kind==='activate'){win?.restore();win?.show();win?.focus();}else send(e);}catch{}}).on('close',()=>app.quit());
ipcMain.on('command',(event,value)=>{if(event.sender!==win.webContents)return; bridge.write(JSON.stringify(value)+'\n');});
app.whenReady().then(()=>{win=new BrowserWindow({width:1440,height:940,minWidth:900,minHeight:620,backgroundColor:'#141416',title:'Frontier',autoHideMenuBar:true,webPreferences:{preload:join(__dirname,'preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true}});
win.webContents.setWindowOpenHandler(()=>({action:'deny'}));win.webContents.on('will-navigate',e=>e.preventDefault());
win.webContents.once('did-finish-load',()=>{ready=true;pending.splice(0).forEach(send);});
win.loadFile(join(__dirname,'index.html'));});
app.on('window-all-closed',()=>app.quit());
