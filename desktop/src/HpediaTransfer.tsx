import React, {useEffect, useState} from 'react';
import {Button} from './gui/elements/Button';
import {FilePicker,FixedSelect} from './gui/elements/ChoiceElements';

type Item = {id:string; status:string; reason?:string; content:Record<string,unknown>;
  diff?:Record<string,{local:unknown; incoming:unknown}>};
type Review = {operation_id:string; items:Item[]; status:string};
const labels:Record<string,string> = {create:'新增',skip:'内容相同，跳过',different:'版本不同',conflict:'身份冲突',blocked:'等待依赖'};
const display = (value:unknown) => typeof value==='string' ? value : JSON.stringify(value);

function ImportReview({card}:{card:Review}){
  const [choices,setChoices] = useState<Record<string,string>>({});
  const decide = (values:Record<string,string>|null) => window.horizon.send({
    action:'hpedia_import_decide', operation_id:card.operation_id, choices:values
  });
  return <section aria-label="导入确认"><h3>导入确认</h3>
    {card.items.map((item,index)=><div key={index}>
      <strong>{String(item.content.title??item.content.prompt??item.content.question??item.id)}</strong>
      <p>{item.id} · {labels[item.status]} {item.reason}</p>
      {item.diff && Object.entries(item.diff).map(([field,change])=><p key={field}>
        {field} · 本地：{display(change.local)} · 导入：{display(change.incoming)}</p>)}
      {item.status==='different' && <FixedSelect aria-label={item.id+' 保留版本'} value={choices[item.id]??''}
        onChange={event=>setChoices({...choices,[item.id]:event.target.value})}>
        <option value="">选择版本</option><option value="local">保留本地版本</option>
        <option value="incoming">采用导入版本</option></FixedSelect>}
    </div>)}
    <Button disabled={card.items.some(item=>item.status==='different'&&!choices[item.id])}
      onClick={()=>decide(choices)}>确认导入</Button> <Button onClick={()=>decide(null)}>取消导入</Button>
  </section>;
}

export function HpediaTransfer(){
  const [reviews,setReviews] = useState<Review[]>([]);
  const [message,setMessage] = useState('');
  useEffect(()=>{
    const unsubscribe=window.horizon.subscribe(event=>{
      if(event.kind==='hpedia_import_pending') setReviews(event.data);
      if(event.kind==='hpedia_import_review'){
        const card:Review=event.data;
        setReviews(current=>[...current.filter(row=>row.operation_id!==card.operation_id),
          ...(card.status==='pending'?[card]:[])]);
        setMessage(({confirmed:'导入完成',rejected:'已取消导入',expired:'导入确认已过期'} as Record<string,string>)[card.status]??'');
      }
      if(event.kind==='hpedia_export_saved')setMessage('百科文件已保存');
    });
    window.horizon.send({action:'hpedia_import_pending'});
    return unsubscribe;
  },[]);
  return <section aria-label="百科导入导出">
    <Button onClick={()=>window.horizon.send({action:'hpedia_export'})}>导出百科</Button>
    <label>导入百科文件</label>
    <FilePicker aria-label="导入百科文件" accept=".json,application/json" onChange={async event=>{
      const file=event.currentTarget.files?.[0];
      if(!file)return;
      event.currentTarget.value='';
      try{
        const data=JSON.parse(await file.text());
        setMessage('');
        window.horizon.send({action:'hpedia_import',operation_id:crypto.randomUUID(),package:data});
      } catch {setMessage('无法读取文件，请选择百科 JSON 文件');}
    }}/>
    {reviews.map(card=><ImportReview key={card.operation_id} card={card}/>)}
    <output>{message}</output>
  </section>;
}
