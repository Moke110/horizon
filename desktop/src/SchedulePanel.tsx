import React,{useState} from 'react';

export type Schedule={schedule_id:string;version:number;title:string;scope:string;interval_seconds:number;next_at:number;covered_until:number;status:string;active_run?:{status:string}|null};
export function SchedulePanel({schedules,limits,onCommand}:{schedules:Schedule[];limits:{main:number;children:number};onCommand:(command:unknown)=>void}){
 const [editing,setEditing]=useState<Schedule|null>(null),[title,setTitle]=useState(''),[scope,setScope]=useState(''),[minutes,setMinutes]=useState(60);
 const [main,setMain]=useState(limits.main),[children,setChildren]=useState(limits.children);
 const command=(value:object)=>onCommand({action:'schedule_command',command:{schema_version:1,operation_id:crypto.randomUUID(),...value}});
 return <div className="trace"><h2>自动任务</h2><p>开发阶段：按你填写的明确范围执行，不代表知识库 Frontier。</p>
 <form onSubmit={event=>{event.preventDefault();const now=Math.floor(Date.now()/1000);command({... (editing?{schedule_id:editing.schedule_id,expected_version:editing.version}:{start_at:now,next_at:now+minutes*60}),title,scope,interval_seconds:minutes*60});setEditing(null);setTitle('');setScope('');}}>
 <label>计划名称<input required maxLength={200} value={title} onChange={e=>setTitle(e.target.value)}/></label>
 <label>明确任务范围<input required maxLength={16000} value={scope} onChange={e=>setScope(e.target.value)}/></label>
 <label>执行间隔（分钟）<input type="number" required min={1} step={1} value={minutes} onChange={e=>setMinutes(Number(e.target.value))}/></label>
 <button type="submit">{editing?'保存修改':'创建计划'}</button>{editing&&<button type="button" onClick={()=>{setEditing(null);setTitle('');setScope('');}}>取消修改</button>}</form>
 <h3>执行上限</h3><p>当前：主任务 {limits.main}，子代理 {limits.children}。等待中的任务不占执行位。</p>
 <form onSubmit={e=>{e.preventDefault();onCommand({action:'schedule_limits',main,children});}}><label>主任务<input type="number" required min={1} max={32} step={1} value={main} onChange={e=>setMain(Number(e.target.value))}/></label><label>子代理<input type="number" required min={1} max={32} step={1} value={children} onChange={e=>setChildren(Number(e.target.value))}/></label><button>保存上限</button></form>
 {schedules.length===0&&<p>尚无计划。</p>}{schedules.map(s=><section className="child-result" key={s.schedule_id}><h3>{s.title}</h3><p>{s.scope}</p><p>状态：{s.status} · 执行：{s.active_run?.status||'等待'} · 间隔 {s.interval_seconds/60} 分钟</p><p>已覆盖至：{new Date(s.covered_until*1000).toLocaleString()} · 下次：{new Date(s.next_at*1000).toLocaleString()}</p>{s.active_run?.status==='coverage_pending'&&<><p>缺少有效的执行覆盖声明，范围未推进。放弃本次执行后计划会暂停。</p><button onClick={()=>command({schedule_id:s.schedule_id,expected_version:s.version,action:'abandon_run'})}>放弃待复核执行</button></>}<button onClick={()=>{setEditing(s);setTitle(s.title);setScope(s.scope);setMinutes(s.interval_seconds/60);}}>修改</button><button onClick={()=>command({schedule_id:s.schedule_id,expected_version:s.version,action:s.status==='paused'?'resume':'pause'})}>{s.status==='paused'?'恢复计划':'暂停计划'}</button></section>)}</div>;
}
