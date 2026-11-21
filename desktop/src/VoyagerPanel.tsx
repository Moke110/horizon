import React,{useEffect,useState} from 'react';
import {VoyagerSourceSettings} from './VoyagerSourceSettings';
const localInput=(d:Date)=>new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);
const labels:Record<string,string>={planned:'待执行',running:'执行中',complete:'已完成',partial:'部分覆盖',blocked:'来源未就绪',failed:'执行失败',cancelled:'已取消',uncertain:'结果不确定',pending:'待调用',unconfigured:'未配置',unknown:'结果未知',reserved:'已预留',received:'已收到',foundation:'基础',recent:'近期',incremental:'增量',seconds_budget:'时间到限',queries_budget:'查询次数到限',candidates_budget:'候选数到限',fulltexts_budget:'正文数到限',provider_failed:'来源调用失败',timeout:'来源超时',authentication:'来源认证失败',budget_unknown:'来源额度未知',fulltext_pending:'正文等待回执',fulltext_failed:'正文获取失败'};
const academicLabels:Record<string,string>={rate_limited:'来源限流',search_metadata:'搜索片段（元数据）',preprint:'预印本',article:'文章',not_peer_reviewed:'未经同行评审',published_at:'发表时间',abstract:'摘要',doi:'DOI',title:'标题',authors:'作者',journal:'期刊'};
const selectionLabels:Record<string,string>={scope_match:'匹配检索范围',counterevidence:'候选反证',correction:'候选更正',retraction:'候选撤稿'};
type CandidateGroup={identity:string;change:string;candidate_indexes:number[];is_new:boolean;scope_links:{scope_id:string;request_id:string}[]};
const label=(s:string)=>academicLabels[s]||labels[s]||s;
const budget=(b:any)=>`${b.seconds} 秒 · ${b.queries} 次查询 · ${b.candidates} 个候选 · ${b.fulltexts} 篇正文`;
const date=(s:number|null|undefined)=>s==null?'未知':new Date(s*1000).toLocaleString();
export type VoyagerSummary={request_id:string;version:number;status:string;scope_text:string;delivery_status?:string;stop_reason?:string};
export function VoyagerPanel({requests,busy,selected,history,taskId,onPrepare,onCommand}:{requests:VoyagerSummary[];busy:string[];selected:any;history:any;taskId?:string;onPrepare:(id:string)=>void;onCommand:(c:any)=>void}){
 const [scope,setScope]=useState(''),[alternatives,setAlternatives]=useState(''),[must,setMust]=useState(''),[exclude,setExclude]=useState(''),[sources,setSources]=useState('arxiv'),[mode,setMode]=useState('initial'),[basis,setBasis]=useState('published'),[from,setFrom]=useState(localInput(new Date(new Date().getFullYear(),0,1))),[until,setUntil]=useState(localInput(new Date())),[overlap,setOverlap]=useState(86400),[limits,setLimits]=useState({seconds:900,queries:20,candidates:100,fulltexts:20}),[ack,setAck]=useState(false);
 useEffect(()=>setAck(false),[selected?.request_id,selected?.version]);
 const lines=(s:string)=>s.split('\n').map(v=>v.trim()).filter(Boolean);
 const command=(action:string,extra={})=>onCommand({action,schema_version:1,operation_id:crypto.randomUUID(),request_id:selected?.request_id,expected_version:selected?.version,...extra});
 return <div className="trace"><h2>范围检索</h2><p>临时明确范围，不会建立 Frontier。来源尚未配置时只显示缺口，不会宣称检索完成。</p><VoyagerSourceSettings/><form onSubmit={e=>{e.preventDefault();onCommand({action:'voyager_create',schema_version:1,operation_id:crypto.randomUUID(),scope:{text:scope,alternatives:lines(alternatives),must_include:lines(must),exclude:lines(exclude),sources:sources.split(',').map(v=>v.trim()).filter(Boolean)},window:{mode,basis,from_at:Math.floor(new Date(from).getTime()/1000),until_at:Math.floor(new Date(until).getTime()/1000),overlap_seconds:overlap},limits});}}>
 <label>原始研究范围<textarea required maxLength={1000} value={scope} onChange={e=>setScope(e.target.value)}/></label><label>中英文替代查询（每行一个）<textarea value={alternatives} onChange={e=>setAlternatives(e.target.value)}/></label><label>必须包含（每行一个）<textarea value={must} onChange={e=>setMust(e.target.value)}/></label><label>排除词（每行一个）<textarea value={exclude} onChange={e=>setExclude(e.target.value)}/></label><label>来源标识（逗号分隔）<input required value={sources} onChange={e=>setSources(e.target.value)}/></label>
 <label>方式<select value={mode} onChange={e=>setMode(e.target.value)}><option value="initial">首次：基础与近期</option><option value="incremental">增量：含重叠窗口</option></select></label><label>时间依据<select value={basis} onChange={e=>setBasis(e.target.value)}><option value="published">发表时间</option><option value="discovered">发现时间</option></select></label><label>开始（本地时间）<input required type="datetime-local" value={from} onChange={e=>setFrom(e.target.value)}/></label><label>结束（不含，本地时间）<input required type="datetime-local" value={until} onChange={e=>setUntil(e.target.value)}/></label><label>增量重叠秒数<input type="number" min="0" value={overlap} onChange={e=>setOverlap(Number(e.target.value))}/></label>
 {Object.entries(limits).map(([k,v])=><label key={k}>{({seconds:'时间（秒）',queries:'查询次数',candidates:'候选数',fulltexts:'正文数'} as any)[k]}<input type="number" min="0" max={({seconds:86400,queries:1000,candidates:10000,fulltexts:1000} as any)[k]} value={v} onChange={e=>setLimits({...limits,[k]:Number(e.target.value)})}/></label>)}<button>保存范围与预算</button></form>
 <section><h3>最近 50 条检索记录</h3>{requests.map(r=><button key={r.request_id} onClick={()=>onCommand({action:'voyager_open',request_id:r.request_id})}>{r.scope_text} · {r.delivery_status==='no_new'?'无新增':label(r.status)}</button>)}</section>
 {selected&&<article><h3>{selected.scope.text}</h3><p>状态：{label(selected.status)} · 停止原因：{selected.stop_reason?label(selected.stop_reason):'无'}</p><p>必须包含：{selected.scope.must_include.join('、')||'无'} · 排除：{selected.scope.exclude.join('、')||'无'} · 来源：{selected.scope.sources.join('、')}</p>
 <p>累计使用：{budget(selected.used)}</p><p>每段预算：{budget(selected.limits)}。继续会开启新一段预算，保留此前消耗与缺口。</p><p>近期范围：{date(selected.window.from_at)} 至 {date(selected.window.until_at)}（结束不含），按{selected.window.basis==='published'?'发表':'发现'}时间。{selected.window.mode==='initial'?'基础检索同时包含较早资料。':`增量向前重叠 ${selected.window.overlap_seconds} 秒。`}</p>
 <label><input type="checkbox" checked={ack} onChange={e=>setAck(e.target.checked)}/>确认上次未回执请求的结果不确定，保留已用预算并继续</label><button disabled={busy.includes(selected.request_id)||['complete','cancelled'].includes(selected.status)} onClick={()=>command('voyager_execute',{continue:selected.status!=='planned',acknowledge_unknown:ack})}>{selected.status==='planned'?'开始检索':'继续（新一段预算）'}</button><button disabled={['complete','cancelled'].includes(selected.status)} onClick={()=>command('voyager_cancel')}>取消检索</button>
 {taskId&&selected.task_id===taskId&&!busy.includes(selected.request_id)&&!['planned','running','cancelled'].includes(selected.status)&&<button onClick={()=>onPrepare(selected.request_id)}>在对话中整理 Voyager 资料包</button>}
 <section><h4>成功覆盖</h4>{!Object.keys(selected.successful_cursors).length&&<p>尚无已完成查询。</p>}{selected.queries.filter((q:any)=>selected.successful_cursors[q.query_id]!==undefined).map((q:any)=><p key={q.query_id}>{q.source} · {label(q.phase)} · {q.term}：完成至 {date(selected.successful_cursors[q.query_id])}</p>)}
 <h4>覆盖缺口（{selected.gap_count}，最多显示 100 条）</h4>{selected.gaps.map((g:any,i:number)=><p key={g.query_id||g.work_id||i}>{g.source||'正文获取'} · {label(g.reason)}</p>)}</section>
 <section><h4>查询计划与实际调用</h4>{selected.queries.map((q:any)=><div key={q.query_id}><p>{q.source} · {label(q.phase)} · {q.term} · {label(q.status)} · 已尝试 {q.attempt_count} 次</p>{q.attempts?.length?<details><summary>最近一次实际调用</summary><pre style={{whiteSpace:'pre-wrap'}}>{JSON.stringify(q.attempts[0],null,2)}</pre></details>:<p>尚未调用来源。</p>}<button onClick={()=>onCommand({action:'voyager_history',request_id:selected.request_id,query_id:q.query_id,offset:0})}>查看执行记录</button></div>)}</section>
 <section>
 <h4>候选来源（{selected.candidate_count}，显示前 50 条）</h4>
 {selected.delivery_status==='no_new'&&<p role="status">本轮没有合格新增。可查看已知候选和查询记录。</p>}
 {(selected.candidate_groups as CandidateGroup[]).map(group=>
 <article key={group.identity+':'+group.change}>
 <h5>{selected.candidates[group.candidate_indexes[0]].title}</h5>
 {group.is_new&&<p>本范围新增</p>}
 {group.scope_links.filter(link=>link.request_id!==selected.request_id).map(link=>
   <button key={link.scope_id} onClick={()=>onCommand({action:'voyager_open',request_id:link.request_id})}>查看关联检索范围</button>)}
 {group.candidate_indexes.map(index=>{const c=selected.candidates[index];return <div key={index}>
 {index!==group.candidate_indexes[0]&&c.title!==selected.candidates[group.candidate_indexes[0]].title&&<strong>{c.title}</strong>}
 <p>{c.source} · {c.source_id} · 发表 {date(c.published_at)} · 发现 {date(c.discovered_at)}</p>
 {c.selection_reason&&<p>{selectionLabels[c.selection_reason.kind]} · 命中 {c.selection_reason.matched_terms.join('、')}
 {c.selection_reason.reposted?' · 转载线索，仅作来源说明':''}</p>}
 {selected.queries.filter((q:any)=>c.query_ids.includes(q.query_id)).map((q:any)=>
   <button key={q.query_id} onClick={()=>onCommand({action:'voyager_history',request_id:selected.request_id,query_id:q.query_id,offset:0})}>查询依据 · {q.term}</button>)}
 <p>来源网址：{c.url}</p>
 {c.url?.startsWith('https://')&&<>
 <button disabled={busy.includes(selected.request_id)||selected.status==='planned'||selected.status==='cancelled'} onClick={()=>command('voyager_fulltext',{candidate_source:c.source,candidate_id:c.source_id})}>获取候选开放全文</button>
 <button onClick={()=>onCommand({action:'materials_web',schema_version:1,operation_id:crypto.randomUUID(),url:c.url,dynamic:true})}>用浏览器获取此候选的正文</button>
 </>}
 {selected.fulltext_work?.filter((item:any)=>item.candidate_source===c.source&&item.candidate_id===c.source_id).map((item:any)=><p key={item.work_id}>{item.status==='succeeded'?'已取得全文':item.status==='failed'?'待全文':label(item.status)}{item.evidence_ref?.startsWith('material:')&&<button onClick={()=>onCommand({action:'materials_open',material_id:item.evidence_ref.slice(9)})}>查看资料与获取记录</button>}</p>)}
 {c.doi&&<p>DOI：{c.doi}</p>}
 {Array.isArray(c.reference_ids)&&<details><summary>来源提供的引用关系</summary>
 <p>参考文献：已列出 {c.reference_ids.length} 个标识，来源报告 {c.reference_count??'未知'} 条。被引用次数：{c.cited_by_count??'未知'}。</p>
 {c.reference_ids.map((id:string,i:number)=><p key={i}>{id}</p>)}</details>}
 <p>资料类型：{label(c.source_type)||'未知'} · 同行评审：{c.peer_review_status==='unknown'?'未知':label(c.peer_review_status)||'未知'}</p>
 {c.missing_fields?.length>0&&<p>来源缺少：{c.missing_fields.map(label).join('、')}</p>}
 </div>;})}</article>)}</section>
 {history&&<details open><summary>查询执行记录（共 {history.total} 次，从第 {history.offset+1} 次起）</summary><pre style={{whiteSpace:'pre-wrap'}}>{JSON.stringify(history.items,null,2)}</pre><button disabled={history.offset===0} onClick={()=>onCommand({action:'voyager_history',request_id:history.request_id,query_id:history.query_id,offset:Math.max(0,history.offset-10)})}>上一页</button><button disabled={history.offset+history.items.length>=history.total} onClick={()=>onCommand({action:'voyager_history',request_id:history.request_id,query_id:history.query_id,offset:history.offset+10})}>下一页</button></details>}</article>}
 </div>;
}
