import {PdfReader,type PdfInfo} from './PdfReader';
import React,{useState} from 'react';

export type Material={material_id:string;title:string;source:{source_id:string;kind:string;label:string;received_at:string};snapshot:{snapshot_id:string;content_hash:string;extraction_version:string;byte_length:number};summary:string|null};
export type MaterialPackage=Material&{original_text:string;version:number;pdf?:PdfInfo};
export function MaterialsPanel({materials,selectedPackage,onCommand}:{materials:Material[];selectedPackage?:MaterialPackage|null;onCommand:(command:unknown)=>void}){
 const [title,setTitle]=useState(''),[label,setLabel]=useState('用户粘贴'),[text,setText]=useState('');
 return <div className="trace"><h2>资料</h2><p>粘贴原文可离线登记，不会自动联网或生成摘要。</p>
 <button onClick={()=>onCommand({action:"choose_pdf"})}>导入 PDF</button>
 <form onSubmit={e=>{e.preventDefault();onCommand({action:'materials_register',schema_version:1,operation_id:crypto.randomUUID(),title,source_label:label,text});}}>
  <label>资料标题<input required maxLength={500} value={title} onChange={e=>setTitle(e.target.value)}/></label>
  <label>来源说明<input required maxLength={2000} value={label} onChange={e=>setLabel(e.target.value)}/></label>
  <label>原文<textarea required aria-label="资料原文" value={text} onChange={e=>setText(e.target.value)}/></label>
  <button type="submit">离线登记资料</button>
 </form>
 <section aria-label="资料列表"><h3>已登记资料</h3>{!materials.length&&<p>尚无资料。</p>}{materials.map(m=><button key={m.material_id} onClick={()=>onCommand({action:'materials_open',material_id:m.material_id})}>{m.title} · {m.source.label}</button>)}</section>
 {selectedPackage&&<article><h3>{selectedPackage.title}</h3><section aria-label="来源身份"><h4>来源</h4><p>用户输入 · {selectedPackage.source.label}</p>{selectedPackage.source.received_at&&<p>登记时间：{selectedPackage.source.received_at}</p>}<p>来源 ID：{selectedPackage.source.source_id}</p>{selectedPackage.snapshot.snapshot_id&&<p>快照 ID：{selectedPackage.snapshot.snapshot_id}</p>}<p>SHA-256：{selectedPackage.snapshot.content_hash}</p><p>提取版本：{selectedPackage.snapshot.extraction_version}</p></section>
 {selectedPackage.pdf?<PdfReader key={selectedPackage.material_id} id={selectedPackage.material_id} info={selectedPackage.pdf} text={selectedPackage.original_text} version={selectedPackage.version} send={onCommand}/>:<section aria-label="资料原文"><h4>原文</h4><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{selectedPackage.original_text}</pre></section>}
 <section aria-label="生成摘要"><h4>生成摘要</h4><p>{selectedPackage.summary===null?'尚未生成摘要':selectedPackage.summary}</p></section></article>}
 </div>;
}
