import {TextInput} from './gui/elements/FormElements';
import {TestTag} from './TestCleanupPanel';
import React,{useState} from 'react';
import {renderLocalHtml} from './LocalHtml';
import {PdfReader} from './PdfReader';

export type ArchiveSource={
  tags?:string[];
  source_id:string;
  title:string;
  label:string;
  url:string|null;
  material_id:string|null;
  version:number;
  summary?:string;
  citation?:string|null;
  sha256?:string;
  file_path?:string|null;
  media_type?:string;
};

export type ArchiveDocument={document:ArchiveSource;available:boolean;original:string|null};

export const ArchiveDocumentView=React.memo(function ArchiveDocumentView({entry,send}:{entry:ArchiveDocument;send:(command:unknown)=>void}){
  const {document,available,original}=entry;
  const mediaType=document.media_type??'';
  let preview:React.ReactNode=null;
  if(available && mediaType==='application/pdf'){
    preview=<PdfReader key={document.source_id} id={document.source_id} send={send}/>;
  }else if(original && mediaType.startsWith('image/')){
    preview=<img src={`data:${mediaType};base64,${original}`} alt={document.title}/>;
  }else if(original){
    const raw=Uint8Array.from(atob(original),character=>character.charCodeAt(0));
    preview=renderLocalHtml(new TextDecoder().decode(raw));
  }
  return <section aria-label="文档条目">
    <h3>{document.title}</h3>
    <p>{document.summary}</p>
    <dl style={{overflowWrap:'anywhere'}}>
      <dt>文档 ID</dt><dd>{document.source_id}</dd>
      <dt>SHA-256</dt><dd>{document.sha256}</dd>
      {document.file_path&&<><dt>文件地址</dt><dd>{document.file_path}</dd></>}
      {document.citation&&<><dt>学术引用</dt><dd>{document.citation}</dd></>}
    </dl>
    {available?<article className="local-html" aria-label="文档原文">{preview}</article>:<p role="status">原文不可用</p>}
  </section>;
});

export function ArchiveSourcesPanel({sources,onOpen,send}:{
  sources:ArchiveSource[];
  onOpen:(source:ArchiveSource)=>void;
  send:(command:unknown)=>void;
}){
  const [editing,setEditing]=useState<ArchiveSource|null>(null);
  const [creating,setCreating]=useState(false);
  const close=()=>{setEditing(null);setCreating(false);};
  return <section className="archive-sources" aria-label="资料库来源">
    <h2>资料库</h2>
    <button type="button" onClick={()=>{setEditing(null);setCreating(true);}}>登记文档</button>
    {(editing||creating)&&<form key={editing?.source_id??'new-source'} onSubmit={event=>{
      event.preventDefault();
      const form=new FormData(event.currentTarget);
      if(editing){
        send({action:'source_update',operation_id:crypto.randomUUID(),source_id:editing.source_id,
          expected_version:editing.version,title:form.get('title'),label:form.get('label')});
      }else{
        send({action:'choose_archive_source',title:form.get('title'),summary:form.get('summary'),
          citation:form.get('citation')||null});
      }
      close();
    }}>
      <label>标题<TextInput name="title" required maxLength={500} defaultValue={editing?.title}/></label>
      {editing?<label>来源说明<TextInput name="label" required maxLength={2000} defaultValue={editing.label}/></label>:<>
        <label>简介<TextInput name="summary" required maxLength={2000}/></label>
        <label>学术引用<TextInput name="citation" maxLength={2000}/></label>
      </>}
      <button type="submit">{editing?'提交审核':'选择原文并提交审核'}</button>
      <button type="button" onClick={close}>取消</button>
    </form>}
    {sources.length===0?<p>尚无来源。</p>:<ul>
      {sources.map(source=><li key={source.source_id}>
        <button type="button" onClick={()=>onOpen(source)}>{source.title} <TestTag tags={source.tags}/> · {source.sha256?'打开文档':'打开原文'}</button>
        <span>{source.label}</span>
        {!source.sha256&&<button type="button" onClick={()=>{setCreating(false);setEditing(source);}}>编辑</button>}
        <button type="button" onClick={()=>send({action:'source_delete',operation_id:crypto.randomUUID(),
          source_id:source.source_id,expected_version:source.version})}>删除来源</button>
      </li>)}
    </ul>}
  </section>;
}
