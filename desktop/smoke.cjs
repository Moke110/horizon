const {app}=require('electron');const fs=require('node:fs');
const timeout=setTimeout(()=>{fs.writeFileSync(process.env.HORIZON_SMOKE_RESULT,'timeout');app.exit(1);},15000);
app.on('browser-window-created',(_,win)=>win.webContents.once('did-finish-load',async()=>{try{
 await new Promise(r=>setTimeout(r,500));
 const result=await win.webContents.executeJavaScript(`(async()=>{const wait=()=>new Promise(r=>setTimeout(r,150));const buttons=()=>[...document.querySelectorAll('button')];if(!document.querySelector('.welcome'))throw Error('No welcome');document.querySelector('button.settings').click();await wait();if(!document.querySelector('input[type=password]'))throw Error('No settings');document.querySelector('.modal button[type=button]').click();await wait();buttons().find(b=>b.textContent==='Console').click();await wait();if(!document.querySelector('.console'))throw Error('No console');return 'UI smoke passed: welcome, settings, cancel, Console';})()`);
 fs.writeFileSync(process.env.HORIZON_SMOKE_SCREENSHOT,(await win.webContents.capturePage()).toPNG());fs.writeFileSync(process.env.HORIZON_SMOKE_RESULT,result);clearTimeout(timeout);win.close();
 }catch(e){fs.writeFileSync(process.env.HORIZON_SMOKE_RESULT,String(e));app.exit(1);}}));
require('./dist/main.cjs');
