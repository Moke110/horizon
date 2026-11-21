import {PdfReader,type PdfInfo} from './PdfReader';
import React,{useState} from 'react';
import {PendingMaterialActions} from './PendingMaterialActions';

export type Material={material_id:string;title:string;source:{source_id:string;kind:string;label:string;received_at:string};snapshot:{snapshot_id:string;content_hash:string;extraction_version:string;byte_length:number};summary:string|null};
export type MaterialSummary=Pick<Material,'material_id'|'title'>&{source:{label:string}};
type AcademicReceipt={supplied_material_id?:string;abstract_authorizations?:{task_id:string;request_id:string;abstract:string;limitation:string}[];identity:string;status:string;format:string;url:string;notice_type?:string|null;
 attempts:{stage:string;outcome:string;url?:string;http_status?:number}[];
 provenance:{request_id:string;task_id?:string;source:string;source_id:string;source_type?:string;related_dois?:string[];reference_ids:string[];reference_count?:number|null;cited_by_count?:number|null}[]};
export type MaterialPackage=Material&{original_text:string;version:number;web?:{status:string;url:string;reason?:string;anchors:{id:string;start:number;end:number}[];metadata:Record<string,string>};pdf?:PdfInfo;academic?:AcademicReceipt};
const webReasons:Record<string,string>={redirect_limit:'网页重定向次数过多',redirect_refused:'目标地址不符合公开页面要求',login_required:'需要登录',payment_required:'需要订阅或付费',access_denied:'来源拒绝访问',restricted_page:'来源要求登录或付费',not_html:'该链接不是 HTML 网页',no_article_body:'未发现可提取的正文',no_bounded_article_body:'正文为空或超过限制',fetch_failed:'网页获取失败',browser_unavailable:'动态页面浏览器未就绪',dynamic_capture_failed:'动态页面获取失败'};
const academicStage:Record<string,string>={native:'来源原生全文',openalex:'OpenAlex 开放位置',unpaywall:'Unpaywall 开放位置',openalex_open_location:'OpenAlex 正文链接',unpaywall_open_location:'Unpaywall 正文链接',landing:'落地页'};
const academicOutcome:Record<string,string>={received:'已取得',redirected:'重定向',unavailable:'不可用',body_not_verified:'未能验证正文',redirect_limit:'重定向次数过多',unconfigured:'来源未配置',no_open_location:'未发现开放位置',location_found:'找到开放位置',pdf_text_unavailable:'PDF 文本待获取'};
export function MaterialsPanel({jobs,materials,selectedPackage,taskId,onCommand}:{jobs:string[];materials:MaterialSummary[];selectedPackage?:MaterialPackage|null;taskId?:string;onCommand:(command:unknown)=>void}){
 const webText=React.useMemo(()=>selectedPackage?.web?Array.from(selectedPackage.original_text):[],[selectedPackage]);
 const [url,setUrl]=useState(''),[dynamic,setDynamic]=useState(false);
 const [title,setTitle]=useState(''),[label,setLabel]=useState('用户粘贴'),[text,setText]=useState('');
 return <div className="trace"><h2>资料</h2><p>粘贴原文可离线登记，不会自动联网或生成摘要。</p>
 <form onSubmit={e=>{e.preventDefault();onCommand({action:'materials_web',schema_version:1,operation_id:crypto.randomUUID(),url,dynamic});}}>
 <label>网页原链接<input required type="url" pattern="https://.+" value={url} onChange={e=>setUrl(e.target.value)}/></label>
 <label><input type="checkbox" checked={dynamic} onChange={e=>setDynamic(e.target.checked)}/>使用浏览器渲染公开动态页</label>
 <button disabled={jobs.length>0}>获取网页正文</button></form>
 {jobs.map(id=><button key={id} onClick={()=>onCommand({action:'materials_web_cancel',operation_id:id})}>取消网页获取</button>)}
 <button onClick={()=>onCommand({action:"choose_pdf"})}>导入 PDF</button>
 <form onSubmit={e=>{e.preventDefault();onCommand({action:'materials_register',schema_version:1,operation_id:crypto.randomUUID(),title,source_label:label,text});}}>
  <label>资料标题<input required maxLength={500} value={title} onChange={e=>setTitle(e.target.value)}/></label>
  <label>来源说明<input required maxLength={2000} value={label} onChange={e=>setLabel(e.target.value)}/></label>
  <label>原文<textarea required aria-label="资料原文" value={text} onChange={e=>setText(e.target.value)}/></label>
  <button type="submit">离线登记资料</button>
 </form>
 <section aria-label="资料列表"><h3>已登记资料</h3>{!materials.length&&<p>尚无资料。</p>}{materials.map(m=><button key={m.material_id} onClick={()=>onCommand({action:'materials_open',material_id:m.material_id,task_id:taskId})}>{m.title} · {m.source.label}</button>)}</section>
 {selectedPackage&&<article><h3>{selectedPackage.title}</h3><section aria-label="来源身份"><h4>来源</h4><p>{selectedPackage.web?'网页来源':'用户输入'} · {selectedPackage.source.label}</p>{selectedPackage.source.received_at&&<p>登记时间：{selectedPackage.source.received_at}</p>}<p>来源 ID：{selectedPackage.source.source_id}</p>{selectedPackage.snapshot.snapshot_id&&<p>快照 ID：{selectedPackage.snapshot.snapshot_id}</p>}<p>SHA-256：{selectedPackage.snapshot.content_hash}</p><p>提取版本：{selectedPackage.snapshot.extraction_version}</p></section>
 {selectedPackage.web?<section><h4>网页正文快照</h4><p>{selectedPackage.web.url}</p>{selectedPackage.web.metadata.author&&<p>作者：{selectedPackage.web.metadata.author}</p>}{selectedPackage.web.metadata.date&&<p>发表日期：{selectedPackage.web.metadata.date}</p>}{selectedPackage.web.status==='unavailable'?<p role="alert">未取得正文：{webReasons[selectedPackage.web.reason||""]||selectedPackage.web.reason}</p>:selectedPackage.web.anchors.map(a=><p key={a.id} id={a.id}><strong>{a.id}</strong> {webText.slice(a.start,a.end).join('')}</p>)}</section>:selectedPackage.pdf?<PdfReader key={selectedPackage.material_id} id={selectedPackage.material_id} info={selectedPackage.pdf} text={selectedPackage.original_text} version={selectedPackage.version} send={onCommand}/>:<section aria-label="资料原文"><h4>原文</h4><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{selectedPackage.original_text}</pre></section>}
 {selectedPackage.academic&&<section aria-label="学术全文获取"><h4>{({'pending_fulltext':'待全文','available':'已取得学术全文','abandoned':'已放弃','supplemented':'已补充全文'} as Record<string,string>)[selectedPackage.academic.status]}</h4>
 <p>{selectedPackage.academic.identity} · 版本 {selectedPackage.version} · {selectedPackage.academic.url}</p>
 {selectedPackage.academic.notice_type&&<p role="status">出版方通知：{({'correction':'更正','retraction':'撤稿','expression-of-concern':'关注声明'} as Record<string,string>)[selectedPackage.academic.notice_type]||selectedPackage.academic.notice_type}</p>}
 <details><summary>获取记录</summary><ul>{selectedPackage.academic.attempts.map((attempt,i)=><li key={i}>{academicStage[attempt.stage]||attempt.stage} · {academicOutcome[attempt.outcome]||attempt.outcome}{attempt.http_status? ` · HTTP ${attempt.http_status}`:''}{attempt.url? ` · ${attempt.url}`:''}</li>)}</ul></details>
 <details><summary>来源与引用覆盖</summary><ul>{selectedPackage.academic.provenance.map((origin,i)=><li key={i}>{origin.source} · {origin.source_id} · 检索 {origin.request_id} · 已列出参考文献 {origin.reference_ids.length} / 来源报告 {origin.reference_count??'未知'} 条 · 被引用 {origin.cited_by_count??'未知'} 次{Boolean(origin.related_dois?.length)&&<p>来源提供的关联 DOI：{origin.related_dois?.join('、')}</p>}</li>)}</ul></details>
 </section>}
 <PendingMaterialActions key={selectedPackage.material_id} material={selectedPackage} taskId={taskId} send={onCommand}/>
 <section aria-label="生成摘要"><h4>生成摘要</h4><p>{selectedPackage.summary===null?'尚未生成摘要':selectedPackage.summary}</p></section></article>}
 </div>;
}
