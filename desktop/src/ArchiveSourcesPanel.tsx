import React,{useState} from 'react';
import type {MaterialSummary} from './MaterialsPanel';

export type ArchiveSource={
  source_id:string;
  title:string;
  label:string;
  url:string|null;
  material_id:string|null;
  version:number;
};

export function ArchiveSourcesPanel({sources,materials,onOpen,send}:{
  sources:ArchiveSource[];
  materials:MaterialSummary[];
  onOpen:(source:ArchiveSource)=>void;
  send:(command:unknown)=>void;
}){
  const [editing,setEditing]=useState<ArchiveSource|null>(null);
  const [creating,setCreating]=useState(false);
  const close=()=>{setEditing(null);setCreating(false);};
  return <section className="archive-sources" aria-label="资料库来源">
    <h2>资料库</h2>
    <button type="button" onClick={()=>{setEditing(null);setCreating(true);}}>新增来源</button>
    {(editing||creating)&&<form key={editing?.source_id??'new-source'} onSubmit={event=>{
      event.preventDefault();
      const form=new FormData(event.currentTarget);
      send({action:editing?'source_update':'source_create',operation_id:crypto.randomUUID(),
        ...(editing?{source_id:editing.source_id,expected_version:editing.version}:{}),
        title:form.get('title'),label:form.get('label'),url:form.get('url')||null,
        material_id:form.get('material_id')||null});
      close();
    }}>
      <label>标题<input name="title" required maxLength={500} defaultValue={editing?.title}/></label>
      <label>来源说明<input name="label" required maxLength={2000} defaultValue={editing?.label}/></label>
      <label>原始链接<input name="url" type="url" maxLength={4096} defaultValue={editing?.url??''}/></label>
      <label>原文文件<select name="material_id" defaultValue={editing?.material_id??''}>
        <option value="">选择原文文件</option>
        {editing?.material_id&&!materials.some(material=>material.material_id===editing.material_id)&&
          <option value={editing.material_id}>已有原文关联</option>}
        {materials.map(material=><option key={material.material_id} value={material.material_id}>{material.title}</option>)}
      </select></label>
      <button type="submit">提交审核</button>
      <button type="button" onClick={close}>取消</button>
    </form>}
    {sources.length===0?<p>尚无来源。</p>:<ul>
      {sources.map(source=><li key={source.source_id}>
        <button type="button" onClick={()=>onOpen(source)}>{source.title} · 打开原文</button>
        <span>{source.label}</span>
        <button type="button" onClick={()=>{setCreating(false);setEditing(source);}}>编辑</button>
        <button type="button" onClick={()=>send({action:'source_delete',operation_id:crypto.randomUUID(),
          source_id:source.source_id,expected_version:source.version})}>删除来源</button>
      </li>)}
    </ul>}
  </section>;
}
