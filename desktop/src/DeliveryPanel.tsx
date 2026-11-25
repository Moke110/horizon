import {Checkbox} from './gui/elements/ChoiceElements';
import React, {useState} from 'react';

type Delivery = {delivery_id:string; version:number; status:string; attempts:unknown[]; receipts:unknown[]; checked?:boolean};
export function DeliveryPanel({deliveries, enabled, completedTurn, onCommand}:{deliveries:Delivery[]; enabled:boolean; completedTurn?:string; onCommand:(command:unknown)=>void}) {
  const [consent,setConsent]=useState<Record<string,boolean>>({});
  const act=(action:string,d?:Delivery)=>onCommand({action,schema_version:1,operation_id:crypto.randomUUID(),...(d?{delivery_id:d.delivery_id,expected_version:d.version}:{turn_id:completedTurn}),accept_duplicate_risk:d?consent[d.delivery_id]===true:false});
  const names:Record<string,string>={queued:'等待发送',sending:'结果不明，需核实',unknown:'结果不明，需核实',sent:'投递成功',failed:'已确认发送失败'};
  return <section aria-label="投递状态"><h3>投递</h3><p>生成完成与投递成功分别记录；改写不会自动发送。</p>
    {!enabled&&<p>尚未配置投递渠道。</p>}
    {enabled&&completedTurn&&<button onClick={()=>act('enqueue_delivery')}>准备投递已完成结果</button>}
    {deliveries.map(d=><div key={d.delivery_id}><p>{names[d.status]||d.status} · 尝试 {d.attempts.length} · 回执 {d.receipts.length}</p>
      {enabled&&['queued','failed'].includes(d.status)&&d.attempts.length<3&&<button onClick={()=>act('send_delivery',d)}>发送</button>}
      {enabled&&['sending','unknown'].includes(d.status)&&<button onClick={()=>act('reconcile_delivery',d)}>核实投递结果</button>}
      {enabled&&d.status==='unknown'&&d.checked&&<><Checkbox checked={!!consent[d.delivery_id]} onChange={e=>setConsent({...consent,[d.delivery_id]:e.target.checked})}>无法证明是否送达，重发可能造成重复。我确认承担重复风险。</Checkbox><button disabled={!consent[d.delivery_id]} onClick={()=>act('resend_delivery',d)}>授权再次发送</button></>}
    </div>)}
  </section>;
}
