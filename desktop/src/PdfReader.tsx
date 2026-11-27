import {MultilineInput} from './gui/elements/FormElements';
import React,{useEffect,useRef,useState} from 'react';
import type {PDFDocumentLoadingTask, RenderTask, PDFWorker} from 'pdfjs-dist';
export type PdfInfo={status:string;reason:string;anchors:{page:number;start:number;end:number}[]};
const reasons:Record<string,string>={encrypted:'文件已加密',page_limit:'超过页数限制',scan_or_empty_page:'含扫描页或空白页',text_limit:'提取文本超过限制',damaged_or_unsupported:'文件损坏或格式不支持',extraction_quality_unverified:'请对照原页核实分栏顺序、公式和表格'};
export function PdfReader({id,info,text='',version,send}:{id:string;info?:PdfInfo;text?:string;version?:number;send:(v:unknown)=>void}){
 const [page,setPage]=useState(1),[error,setError]=useState(''),[supplement,setSupplement]=useState('');
 const [pageCount,setPageCount]=useState(0);
 const canvas=useRef<HTMLCanvasElement>(null);
 useEffect(()=>{
  let disposed=false,task:PDFDocumentLoadingTask|undefined,render:RenderTask|undefined,port:Worker|undefined,worker:PDFWorker|undefined;
  setError('');
  (async()=>{
   const modulePath='./pdf.mjs';
   const lib:typeof import('pdfjs-dist')=await import(modulePath);
   if(disposed)return;
   const response=await fetch(new URL('original/'+id,document.baseURI));
   if(!response.ok)throw Error('Unavailable PDF');
   const data=new Uint8Array(await response.arrayBuffer());
   if(disposed)return;
   port=new Worker(new URL('pdf.worker.mjs',document.baseURI),{type:'module'});
   worker=lib.PDFWorker.create({port});
   task=lib.getDocument({data,worker,useWorkerFetch:true,cMapUrl:new URL('cmaps/',document.baseURI).href,cMapPacked:true,standardFontDataUrl:new URL('standard_fonts/',document.baseURI).href,useWasm:false});
   const doc=await task.promise;
   if(disposed)return;
   setPageCount(doc.numPages);
   const pdfPage=await doc.getPage(page);
   if(disposed||!canvas.current)return;
   const base=pdfPage.getViewport({scale:1});
   const viewport=pdfPage.getViewport({scale:Math.min(1.5,1600/base.width,2200/base.height)});
   canvas.current.width=Math.ceil(viewport.width);canvas.current.height=Math.ceil(viewport.height);
   render=pdfPage.render({canvas:canvas.current,viewport});await render.promise;
  })().catch(()=>{if(!disposed)setError('无法显示该 PDF 原页。');});
  return ()=>{disposed=true;render?.cancel();void Promise.resolve(task?.destroy()).finally(()=>{worker?.destroy();port?.terminate();});};
 },[id,page]);
 const resolve=(decision:string)=>send({action:'materials_pdf_resolve',decision,material_id:id,expected_version:version,schema_version:1,operation_id:crypto.randomUUID(),...(decision==='supplement'?{text:supplement}:{})});
 const anchor=info?.anchors.find(a=>a.page===page);
 if(!info)return <section><h4>PDF 原页</h4>
  <button type="button" disabled={page<=1} onClick={()=>setPage(page-1)}>上一页</button>
  <span>第 {page} 页</span>
  <button type="button" disabled={page>=pageCount} onClick={()=>setPage(page+1)}>下一页</button>
  {error?<p role="alert">{error}</p>:<canvas aria-label={'PDF 第 '+page+' 页'} ref={canvas} style={{maxWidth:'100%',height:'auto'}}/>}
 </section>;
 return <section><h4>PDF 原页</h4><p>{reasons[info.reason]||info.reason}</p>
 {info.anchors.map(a=><button key={a.page} aria-pressed={a.page===page} onClick={()=>setPage(a.page)}>第 {a.page} 页</button>)}
 {error?<p role="alert">{error}</p>:<canvas aria-label={'PDF 第 '+page+' 页'} ref={canvas} style={{maxWidth:'100%',height:'auto'}}/>}
 {info.status==='supplemented'?<><h4>补充文本</h4><pre style={{whiteSpace:'pre-wrap'}}>{text}</pre></>:anchor&&<><h4>第 {page} 页提取文本</h4><pre style={{whiteSpace:'pre-wrap'}}>{Array.from(text).slice(anchor.start,anchor.end).join('')}</pre></>}
 {info.status==='pending_text'&&<><label>补充文本<MultilineInput value={supplement} onChange={e=>setSupplement(e.target.value)}/></label><button disabled={!supplement.trim()} onClick={()=>resolve('supplement')}>保存补充文本</button><button onClick={()=>resolve('abandon')}>放弃补充</button></>}
 {info.status==='abandoned'&&<p>已放弃补充文本。</p>}</section>;
}
