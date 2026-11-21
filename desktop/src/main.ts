import {app,BrowserWindow,dialog,ipcMain,Tray,Menu,nativeImage} from 'electron';
import {join,basename} from 'node:path';
import {open} from 'node:fs/promises';
import {randomUUID} from 'node:crypto';
import {appOrigin,selectOriginal,serveReader} from './pdfProtocol';
import {createInterface} from 'node:readline';
import {connect} from 'node:net';
const bridge=connect({host:'127.0.0.1',port:Number(process.env.HORIZON_DESKTOP_PORT)},()=>{
  bridge.write(process.env.HORIZON_DESKTOP_TOKEN+'\n');
});
let quitting=false;
const quitNow=()=>{quitting=true;app.quit();};
bridge.on('error',quitNow);
let tray:Tray;
const requestExit=()=>bridge.write(JSON.stringify({action:'exit'})+'\n');
let win: BrowserWindow; let ready=false; const pending:unknown[]=[];
const send=(event:unknown)=>{if(ready)win.webContents.send('event',event);else pending.push(event);};
createInterface({input:bridge}).on('line',line=>{try {const e=JSON.parse(line); if(e.kind==='private_pdf'){selectOriginal(e.data);return;} if(e.kind==='exit_ready'){quitNow();}else if(e.kind==='activate'){win?.restore();win?.show();win?.focus();}else send(e);}catch{}}).on('close',quitNow);
ipcMain.on('command',async(event,value)=>{
 if(event.sender!==win.webContents||event.senderFrame!==win.webContents.mainFrame||value?.action==='materials_pdf_register'||value?.action==='materials_pending_resolve'&&value?.decision==='supplement')return;
 if(value?.action==='choose_pdf'||value?.action==='choose_pending_pdf'){
  const choice=await dialog.showOpenDialog(win,{properties:['openFile'],filters:[{name:'PDF',extensions:['pdf']}]});
  if(choice.canceled)return;
  try {
   const file=await open(choice.filePaths[0],'r');
   try {
    const stat=await file.stat();
    if(!stat.isFile()||stat.size>10*1024*1024)throw Error();
    const data=Buffer.alloc(stat.size);
    let read=0;
    while(read<data.length){const result=await file.read(data,read,data.length-read,read);if(!result.bytesRead)throw Error();read+=result.bytesRead;}
    const title=basename(choice.filePaths[0]);
    value=value.action==='choose_pending_pdf'
    ? {action:'materials_pending_resolve',decision:'supplement',operation_id:randomUUID(),schema_version:1,
       material_id:value.material_id,expected_version:value.expected_version,pdf_base64:data.toString('base64')}
    : {action:'materials_pdf_register',operation_id:randomUUID(),schema_version:1,title,source_label:title,pdf_base64:data.toString('base64')};
   }finally{await file.close();}
  }catch{send({kind:'notice',data:'请选择可读取且不超过 10 MiB 的 PDF 文件。'});return;}
 }
 bridge.write(JSON.stringify(value)+'\n');});
app.whenReady().then(()=>{serveReader();win=new BrowserWindow({width:1440,height:940,minWidth:900,minHeight:620,backgroundColor:'#141416',title:'Horizon',autoHideMenuBar:true,webPreferences:{preload:join(__dirname,'preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true}});
tray=new Tray(nativeImage.createFromBitmap(Buffer.from(Array.from({length:16*16},()=>[200,135,105,255]).flat()),{width:16,height:16}));tray.setToolTip('Horizon');tray.setContextMenu(Menu.buildFromTemplate([{label:'打开 Horizon',click:()=>{win.show();win.focus();}},{label:'退出 Horizon（暂停任务）',click:requestExit}]));tray.on('double-click',()=>{win.show();win.focus();});
win.on('close',e=>{if(!quitting){e.preventDefault();win.hide();}});
win.webContents.setWindowOpenHandler(()=>({action:'deny'}));win.webContents.on('will-navigate',e=>e.preventDefault());
win.webContents.once('did-finish-load',()=>{ready=true;pending.splice(0).forEach(send);});
win.loadURL(appOrigin+'/index.html');});
app.on('before-quit',e=>{if(!quitting){e.preventDefault();requestExit();}});
app.on('window-all-closed',()=>{if(quitting)app.quit();});
