import {TextInput} from './gui/elements/FormElements';
import {FixedSelect} from './gui/elements/ChoiceElements';
import React,{useState} from 'react';

export type Subscription={id:string;kind:string;url:string;last_success:number|null;gap:string|null;change_count:number;changes:{title:string;url:string;version:number}[]};
export function SubscriptionPanel({rows,busy,send}:{rows:Subscription[];busy:string[];send:(v:any)=>void}){
 const [kind,setKind]=useState('feed'),[url,setUrl]=useState('');
 return <section className="trace"><h2>订阅更新</h2><form onSubmit={e=>{e.preventDefault();send({action:'subscription_add',kind,url});}}>
 <label>订阅类型<FixedSelect value={kind} onChange={e=>setKind(e.target.value)}><option value="feed">RSS / Atom</option><option value="section">网站栏目</option></FixedSelect></label>
 <label>公开 HTTPS 地址<TextInput required type="url" pattern="https://.+" value={url} onChange={e=>setUrl(e.target.value)}/></label>
 <button>添加订阅</button></form>{rows.map(r=><article key={r.id}><h3>{r.kind==='feed'?'RSS / Atom':'网站栏目'}</h3><p>{r.url}</p><p>范围检索来源：sub_{r.id}</p>
 <p>上次成功：{r.last_success?new Date(r.last_success*1000).toLocaleString():'尚未刷新'}</p>
 {r.gap&&<p role="alert">覆盖缺口：{r.gap}</p>}
 {r.last_success&&!r.gap&&<p>本轮新增或更新 {r.change_count} 条</p>}
 {r.changes.map(c=><p key={c.url+c.title}>{c.title} · 版本 {c.version} · {c.url}</p>)}
 <button disabled={busy.includes(r.id)} onClick={()=>send({action:'subscription_refresh',id:r.id})}>{busy.includes(r.id)?'正在刷新':'刷新订阅'}</button>
 </article>)}</section>;
}
