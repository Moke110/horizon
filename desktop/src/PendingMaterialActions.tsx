import {Collapsible} from './gui/elements/Collapsible';
import {Checkbox, NativeFilePicker} from './gui/elements/ChoiceElements';
import React,{useState} from 'react';
import type {MaterialPackage} from './MaterialsPanel';

export function PendingMaterialActions({material,taskId,send}:{material:MaterialPackage;taskId?:string;send:(command:unknown)=>void}){
 const [confirmed,setConfirmed]=useState(false);
 const academic=material.academic;
 if(!academic)return null;
 if(academic.supplied_material_id)return <button onClick={()=>send({action:'materials_open',material_id:academic.supplied_material_id,task_id:taskId})}>打开补充的全文</button>;
 if(academic.status!=='pending_fulltext')return null;
 const origin=taskId?academic.provenance.find(value=>value.task_id===taskId):undefined;
 const authorization=academic.abstract_authorizations?.find(item=>item.request_id===origin?.request_id&&item.task_id===taskId);
 const context={material_id:material.material_id,expected_version:material.version};
 function resolve(decision:'abandon'|'authorize_abstract'){
  send({...context,action:'materials_pending_resolve',schema_version:1,operation_id:crypto.randomUUID(),
       decision,confirmed,request_id:origin?.request_id,task_id:taskId});
 }
 return <section aria-label="处理待全文资料">
  <p>全文不可用。此资料暂不作为正式报道的正文证据。</p>
  <div>补充 PDF 全文
   <NativeFilePicker key={material.material_id} label="补充 PDF 全文"
    onBrowse={pickerId=>send({...context,action:'choose_pending_pdf',pickerId})}/>
  </div>
  <button onClick={()=>resolve('abandon')}>放弃此资料</button>
  {origin&&(authorization?<Collapsible title="当前任务的摘要解读">
   <p>{authorization.limitation}</p><p>{authorization.abstract}</p></Collapsible>:<>
   <Checkbox checked={confirmed} onChange={event=>setConfirmed(event.target.checked)}>我接受当前任务仅依据来源摘要解读，全文尚未核对。</Checkbox>
   <button disabled={!confirmed} onClick={()=>resolve('authorize_abstract')}>授权摘要解读</button>
  </>)}
 </section>;
}
