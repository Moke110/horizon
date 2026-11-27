import {app,BrowserWindow,dialog,ipcMain,Tray,Menu,nativeImage,shell} from 'electron';
import {join,basename,extname} from 'node:path';
import {open,writeFile} from 'node:fs/promises';
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
const documentTypes:Record<string,string>={
 pdf:'application/pdf',html:'text/html',htm:'text/html',xhtml:'application/xhtml+xml',
 png:'image/png',jpg:'image/jpeg',jpeg:'image/jpeg',gif:'image/gif',webp:'image/webp',
 bmp:'image/bmp',tif:'image/tiff',tiff:'image/tiff',avif:'image/avif',svg:'image/svg+xml',
};
async function saveHpediaExport(data: unknown) {
  try {
    const choice=await dialog.showSaveDialog(win,{defaultPath:'hpedia.json',filters:[{name:'百科 JSON',extensions:['json']}]});
    if(choice.canceled || !choice.filePath)return;
    await writeFile(choice.filePath,JSON.stringify(data,null,2),'utf8');
    send({kind:'hpedia_export_saved',data:(data as {blocked?:unknown[]}).blocked??[]});
  } catch {
    send({kind:'notice',data:'百科文件保存失败'});
  }
}
createInterface({input:bridge}).on('line',line=>{try {const e=JSON.parse(line); if(e.kind==='hpedia_export'){void saveHpediaExport(e.data);return;} if(e.kind==='private_pdf'){selectOriginal(e.data);return;} if(e.kind==='exit_ready'){quitNow();}else if(e.kind==='activate'){win?.restore();win?.show();win?.focus();}else send(e);}catch{}}).on('close',quitNow);
function showWindow() {
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
}
function optionsItems(): Electron.MenuItemConstructorOptions[] {
  return [
    {label:'模型设置',click:()=>{showWindow();send({kind:'open_settings'});}},
    {label:'导出诊断',click:()=>{showWindow();bridge.write(JSON.stringify({action:'export_diagnostics'})+'\n');}},
    {label:'退出Horizon',click:requestExit},
  ];
}
ipcMain.on('command',async(event,value)=>{
 if(event.sender!==win.webContents||event.senderFrame!==win.webContents.mainFrame||value?.action==='materials_pdf_register'||value?.action==='source_register_file'||value?.action==='materials_pending_resolve'&&value?.decision==='supplement')return;
 if(value?.action==='choose_archive_source'){
  const selected=await dialog.showOpenDialog(win,{properties:['openFile'],filters:[{name:'源文档',extensions:Object.keys(documentTypes)}]});
  if(selected.canceled)return;
  try{
   const filePath=selected.filePaths[0];
   const mediaType=documentTypes[extname(filePath).slice(1).toLowerCase()];
   if(!mediaType)throw Error('Unsupported source file');
   const source=await open(filePath,'r');
   try{
    const status=await source.stat();
    if(!status.isFile()||status.size===0||status.size>10*1024*1024)throw Error('Invalid source file');
    const data=Buffer.alloc(status.size);
    const {bytesRead}=await source.read(data);
    if(bytesRead!==status.size||(await source.stat()).size!==status.size)throw Error('Source file changed');
    bridge.write(JSON.stringify({action:'source_register_file',operation_id:randomUUID(),title:value.title,
      summary:value.summary,citation:value.citation,media_type:mediaType,original_base64:data.toString('base64')})+'\n');
   }finally{await source.close();}
  }catch{send({kind:'notice',data:'请选择可读取且不超过 10 MiB 的源文档。'});}
  return;
 }
 if(value?.action==='open_source_url'){
  try{
   const url=new URL(value.url);
   if((url.protocol==='https:'||url.protocol==='http:')&&!url.username&&!url.password)
    await shell.openExternal(url.href);
  }catch{send({kind:'notice',data:'无法打开原始来源链接。'});}
  return;
 }
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
    if (typeof value.pickerId === 'string') {
      send({kind:'desktop_pdf_selected',data:{pickerId:value.pickerId,fileName:title}});
    }
    value=value.action==='choose_pending_pdf'
    ? {action:'materials_pending_resolve',decision:'supplement',operation_id:randomUUID(),schema_version:1,
       material_id:value.material_id,expected_version:value.expected_version,pdf_base64:data.toString('base64')}
    : {action:'materials_pdf_register',operation_id:randomUUID(),schema_version:1,title,source_label:title,pdf_base64:data.toString('base64')};
   }finally{await file.close();}
  }catch{send({kind:'notice',data:'请选择可读取且不超过 10 MiB 的 PDF 文件。'});return;}
 }
 bridge.write(JSON.stringify(value)+'\n');});
app.whenReady().then(()=>{serveReader();win=new BrowserWindow({
  ...(process.platform === 'win32' ? {
    titleBarStyle: 'hidden' as const,
    titleBarOverlay: {color: '#1d1d20', symbolColor: '#e8e8eb', height: 44},
  } : {}),
  width:1440,height:940,minWidth:900,minHeight:620,backgroundColor:'#141416',title:'Horizon',autoHideMenuBar:true,webPreferences:{preload:join(__dirname,'preload.cjs'),contextIsolation:true,nodeIntegration:false,sandbox:true}});
tray=new Tray(nativeImage.createFromBitmap(Buffer.from(Array.from({length:16*16},()=>[200,135,105,255]).flat()),{width:16,height:16}));tray.setToolTip('Horizon');tray.setContextMenu(Menu.buildFromTemplate([{label:'打开 Horizon',click:showWindow},{type:'separator'},...optionsItems()]));tray.on('double-click',()=>{win.show();win.focus();});
let minimizedAt:number|null=null;
win.on('minimize',()=>{minimizedAt??=Date.now();});
win.on('restore',()=>{
  if(minimizedAt!==null && Date.now()-minimizedAt>=60*60*1000)send({kind:'board_reset_type'});
  minimizedAt=null;
});
win.on('close',e=>{if(!quitting){e.preventDefault();win.hide();}});
win.webContents.setWindowOpenHandler(()=>({action:'deny'}));win.webContents.on('will-navigate',e=>e.preventDefault());
win.webContents.once('did-finish-load',()=>{ready=true;pending.splice(0).forEach(send);});
win.loadURL(appOrigin+'/index.html');});
app.on('before-quit',e=>{if(!quitting){e.preventDefault();requestExit();}});
app.on('window-all-closed',()=>{if(quitting)app.quit();});
