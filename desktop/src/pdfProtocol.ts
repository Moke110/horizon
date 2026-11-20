import {protocol,net} from 'electron';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';

export const appOrigin='horizon-app://desktop';
protocol.registerSchemesAsPrivileged([{scheme:'horizon-app',privileges:{standard:true,secure:true,supportFetchAPI:true}}]);
let current:{id:string;bytes:Buffer}|undefined;
export function selectOriginal(value:{id:string;data:string}|null){
 current=value?{id:value.id,bytes:Buffer.from(value.data,'base64')}:undefined;
}
export function serveReader(){
 protocol.handle('horizon-app',async request=>{
  const url=new URL(request.url);
  if(request.method!=='GET'||url.host!=='desktop'||url.username||url.password||url.search)return new Response(null,{status:403});
  if(current && url.pathname==='/original/'+current.id){
   return new Response(new Uint8Array(current.bytes),{headers:{'Content-Type':'application/pdf','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'}});
  }
  const asset=url.pathname.slice(1);
  if(!/^(index\.html|app\.(js|css)|pdf(?:\.worker)?\.mjs|(?:standard_fonts|cmaps)\/[A-Za-z0-9_.-]+\.(?:pfb|ttf|bcmap))$/.test(asset))return new Response(null,{status:404});
  const response=await net.fetch(pathToFileURL(join(__dirname,asset)).href);
  if(asset.endsWith('.mjs'))return new Response(response.body,{status:response.status,headers:{'Content-Type':'text/javascript','X-Content-Type-Options':'nosniff'}});
  return response;
 });
}
