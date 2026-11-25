import {TextInput} from './gui/elements/FormElements';
import {DataTable} from './gui/elements/DataTable';
import {Collapsible} from './gui/elements/Collapsible';
import React,{useEffect,useRef,useState} from 'react';

export type HistoryKind='card'|'theme'|'frontier';
export type HistoryRecord={version?:number;[key:string]:unknown};
export type KnowledgeHistoryEntry={
  version:number;
  action:string;
  actor?:string|null;
  time?:number|null;
  reason?:string|null;
  target_version?:number|null;
  before?:unknown;
  after?:unknown;
  can_preview?:boolean;
};
type KnowledgeHistoryPage={
  operation_id:string;
  kind:HistoryKind;
  id:string;
  current_version?:number;
  total:number;
  offset:number;
  limit:number;
  entries:KnowledgeHistoryEntry[];
};
type UndoChange={field:string;before?:unknown;after?:unknown;current?:unknown;proposed?:unknown;conflict?:boolean};
type UndoPreview={
  operation_id:string;
  kind:HistoryKind;
  id:string;
  target_version:number;
  current_version:number;
  preview_token?:string;
  can_undo?:boolean;
  code?:string|null;
  message?:string;
  changes?:UndoChange[];
};
type UndoResult={operation_id:string;kind:HistoryKind;id:string;record:HistoryRecord;current_version?:number};

type HistoryPending={operation_id:string;kind:HistoryKind;id:string};
type PreviewPending=HistoryPending&{target_version:number};
type UndoPending=PreviewPending&{expected_version:number;preview_token:string};

const HISTORY_LIMIT=10;
const TYPE_LABEL:Record<HistoryKind,string>={card:'知识卡片',theme:'命题',frontier:'关注问题'};
const ACTION_LABEL:Record<string,string>={
  create:'创建',created:'创建',update:'编辑',updated:'编辑',edit:'编辑',
  archive:'归档',archived:'归档',delete:'删除',deleted:'删除',
  undo:'撤销',restore:'撤销',restored:'撤销',group_undo:'Keeper 整组撤销',
};
const CODE_LABEL:Record<string,string>={
  version_conflict:'记录在预览之后又有修改，请重新预览',
  preview_conflict:'撤销预览已失效，请重新查看差异',
  undo_conflict:'目标版本之后的字段修改与该次撤销冲突，不能覆盖',
  undo_not_supported:'该历史操作不支持撤销',
  nothing_to_undo:'当前字段已经是原值',
  invalid_transition:'当前记录已归档或处于终止状态，不能撤销',
  not_supported:'该历史操作不支持撤销',
  invalid_target:'目标版本不可撤销',
  invalid_operation:'撤销请求无效，请重新预览',
  operation_conflict:'该操作编号已被使用，请重新预览',
  not_found:'记录或版本不存在',
};
const FIELD_LABEL:Record<string,string>={
  title:'标题',content:'内容',question:'关注问题',judgement:'判断',status:'状态',
  aliases:'别名',sources:'来源',relations:'关联',questionable:'存疑',
  provenance:'来源性质',source_pending:'来源待补充',known:'是否计入已知',
  conditions:'适用条件',related_cards:'关联知识卡片',evidence:'证据',evidence_sufficiency:'证据充分性',
  scope:'研究范围','scope.text':'研究范围',scope_text:'范围摘要',alternatives:'替代查询',
  'scope.alternatives':'替代查询','scope.sources':'来源标识',
  hard_constraints:'硬约束','hard_constraints.must_include':'必须包含','hard_constraints.exclude':'排除词',must_include:'必须包含',exclude:'排除词',
  origin:'来源方式',created_at:'创建时间',updated_at:'更新时间',
  card_id:'知识卡片编号',theme_id:'命题编号',frontier_id:'关注问题编号',
  material_id:'资料编号',locator:'定位',excerpt:'原文摘录',material_version:'资料版本',
  content_hash:'内容摘要',source_kind:'来源类型',snapshot_id:'快照编号',source_label:'来源标识',
  evidence_id:'证据编号',stance:'立场',reason:'理由',source:'来源',
  schema_version:'数据版本',operation_id:'操作编号',version:'版本',
};
const actionLabel=(value:string)=>{
  if(!value)return '未记录';
  const normalized=value.replace(/^(card|theme|frontier)[_-]/,'');
  return ACTION_LABEL[value]||ACTION_LABEL[normalized]||value;
};
const actorLabel=(value?:string|null)=>{
  if(typeof value!=='string'||!value.trim())return '未记录';
  return value==='explorer'?'探索者':value;
};
const reasonLabel=(value?:string|null)=>typeof value==='string'&&value.trim()?value.trim():'未记录';
const timeLabel=(value?:number|null)=>{
  const seconds=typeof value==='number'?value:typeof value==='string'&&/^\d+$/.test(value)?Number(value):NaN;
  return Number.isFinite(seconds)?new Date(seconds*1000).toLocaleString():'时间未记录';
};
const fieldLabel=(field:string)=>FIELD_LABEL[field]||field;
const formatValue=(value:unknown):string=>{
  if(value===null||value===undefined)return '（空）';
  if(typeof value==='string')return value.trim()?value:'（空）';
  if(typeof value==='number'||typeof value==='boolean')return String(value);
  if(Array.isArray(value)){
    if(!value.length)return '（空）';
    return value.map(item=>formatValue(item)).join('、');
  }
  if(typeof value==='object'){
    const entries=Object.entries(value as Record<string,unknown>);
    if(!entries.length)return '（空）';
    return entries.map(([key,item])=>`${fieldLabel(key)}：${formatFieldValue(key,item)}`).join('\n');
  }
  return String(value);
};
const isRecordValue=(value:unknown):value is Record<string,unknown>=>!!value&&typeof value==='object'&&!Array.isArray(value);
const FIELD_VALUE_LABEL:Record<string,Record<string,string>>={
  status:{active:'有效',archived:'已归档',following:'关注中',paused:'已暂停',resolved:'已解决',deleted:'已删除'},
  stance:{supports:'支持',opposes:'反对'},
  evidence_sufficiency:{balanced:'支持与反对证据并存',single_sided:'证据不足（仅单向）',unsupported:'证据不足（暂无证据）'},
  provenance:{sourced:'有外部依据',user_statement:'用户陈述'},
  origin:{manual:'人工创建'},
};
const formatFieldValue=(field:string,value:unknown):string=>{
  if(typeof value==='number'&&(field==='time'||field.endsWith('_at')))return timeLabel(value);
  if(typeof value==='string'){
    if(field==='time'||field.endsWith('_at')){
      const seconds=/^\d+$/.test(value)?Number(value):NaN;
      if(Number.isFinite(seconds))return timeLabel(seconds);
    }
    const labels=FIELD_VALUE_LABEL[field];
    if(labels&&labels[value])return labels[value];
  }
  return formatValue(value);
};

function Snapshot({title,value}:{title:string;value:unknown}){
  const object=isRecordValue(value)?value:null;
  return <section className="knowledge-history-snapshot">
    <h5>{title}</h5>
    {value===null||value===undefined?<p className="knowledge-history-note">未记录</p>
      :object?<dl>{Object.entries(object).map(([key,item])=><React.Fragment key={key}><dt>{fieldLabel(key)}</dt><dd>{formatFieldValue(key,item)}</dd></React.Fragment>)}</dl>
      :<pre>{formatValue(value)}</pre>}
  </section>;
}

function DiffCell({field,value}:{field:string;value:unknown}){
  return <td>{formatFieldValue(field,value)}</td>;
}

/**
 * Complete version history for one selected knowledge record. The panel only
 * reads through the generic kind/id bridge, previews an undo before offering a
 * separate confirm action, and never retries or forces a conflicting restore.
 */
export function KnowledgeHistoryPanel({
  kind,id,currentVersion,onCommand,onUndoApplied,onClose,
}:{
  kind:HistoryKind;
  id:string;
  currentVersion?:number;
  onCommand:(command:unknown)=>void;
  onUndoApplied:(kind:HistoryKind,id:string,record:HistoryRecord)=>void;
  onClose:()=>void;
}){
  const [page,setPage]=useState<KnowledgeHistoryPage|null>(null);
  const [offset,setOffset]=useState(0);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState('');
  const [preview,setPreview]=useState<UndoPreview|null>(null);
  const [previewing,setPreviewing]=useState(false);
  const [undoing,setUndoing]=useState(false);
  const [undoReason,setUndoReason]=useState('');
  const [message,setMessage]=useState('');
  const [lastTarget,setLastTarget]=useState<number|null>(null);
  const historyPending=useRef<HistoryPending|null>(null);
  const previewPending=useRef<PreviewPending|null>(null);
  const undoPending=useRef<UndoPending|null>(null);
  const command=useRef(onCommand);
  const applied=useRef(onUndoApplied);
  command.current=onCommand;
  applied.current=onUndoApplied;
  const heading=useRef<HTMLHeadingElement>(null);
  const previewHeading=useRef<HTMLHeadingElement>(null);
  const successHeading=useRef<HTMLParagraphElement>(null);

  const send=(action:string,operation_id:string,extra:Record<string,unknown>={})=>{
    command.current({action,schema_version:1,kind,id,operation_id,...extra});
  };
  const requestHistory=(nextOffset:number)=>{
    const operation_id=crypto.randomUUID();
    historyPending.current={operation_id,kind,id};
    setLoading(true);setError('');setOffset(nextOffset);
    send('knowledge_history',operation_id,{offset:nextOffset,limit:HISTORY_LIMIT});
  };
  useEffect(()=>{
    requestHistory(0);
    return ()=>{historyPending.current=null;previewPending.current=null;undoPending.current=null;};
  },[kind,id]);
  useEffect(()=>{heading.current?.focus({preventScroll:true});},[]);
  useEffect(()=>{if(preview)previewHeading.current?.focus();},[preview]);
  useEffect(()=>{if(message.startsWith('已撤销'))successHeading.current?.focus();},[message]);

  useEffect(()=>{
    const unsubscribe=window.horizon.subscribe(event=>{
      const data=event?.data;
      if(!data||typeof data!=='object')return;
      if(event.kind==='knowledge_history'){
        const work=historyPending.current;
        if(!work||data.operation_id!==work.operation_id||data.kind!==work.kind||data.id!==work.id)return;
        historyPending.current=null;
        setLoading(false);setError('');
        setPage({
          ...(data as KnowledgeHistoryPage),
          entries:Array.isArray(data.entries)?data.entries:[],
        });
        setOffset(typeof data.offset==='number'?data.offset:0);
        return;
      }
      if(event.kind==='knowledge_undo_preview'){
        const work=previewPending.current;
        if(!work||data.operation_id!==work.operation_id||data.kind!==work.kind||data.id!==work.id||data.target_version!==work.target_version)return;
        previewPending.current=null;
        setPreviewing(false);
        setPreview({
          ...(data as UndoPreview),
          changes:Array.isArray(data.changes)?data.changes:[],
        });
        setMessage('');
        return;
      }
      if(event.kind==='knowledge_undo'){
        const work=undoPending.current;
        if(!work||data.operation_id!==work.operation_id||data.kind!==work.kind||data.id!==work.id)return;
        undoPending.current=null;
        setUndoing(false);setPreview(null);setLastTarget(null);setUndoReason('');
        const result=data as UndoResult;
        const record=isRecordValue(result.record)?result.record as HistoryRecord:{};
        const version=typeof record.version==='number'?record.version:result.current_version;
        setMessage(`已撤销指定变更，并提交为版本 ${version??'新版本'}。`);
        applied.current(kind,id,record);
        requestHistory(0);
        return;
      }
      if(event.kind==='command_error'&&data.operation_id){
        if(historyPending.current?.operation_id===data.operation_id){
          historyPending.current=null;setLoading(false);
          setError(data.message||data.code||'版本历史未能读取；可重试。');
          return;
        }
        if(previewPending.current?.operation_id===data.operation_id){
          previewPending.current=null;setPreviewing(false);setPreview(null);
          setMessage(`预览未完成：${data.message||data.code||'请重试'}。不会写入任何版本。`);
          return;
        }
        if(undoPending.current?.operation_id===data.operation_id){
          undoPending.current=null;setUndoing(false);setPreview(null);
          const details=data.details;
          const conflict=data.code==='undo_conflict'||data.code==='preview_conflict'||data.code==='version_conflict';
          if(conflict&&details&&Array.isArray(details.changes)&&typeof details.target_version==='number'){
            setLastTarget(details.target_version);
            setPreview({
              operation_id:data.operation_id,kind,id,
              target_version:details.target_version,
              current_version:typeof details.current_version==='number'?details.current_version:0,
              can_undo:false,code:data.code,changes:details.changes,
            });
          }
          if(data.code==='undo_conflict'){
            setMessage('撤销未完成：后续字段修改与该次撤销冲突，已拒绝覆盖。下方标记冲突的字段不会被写入；请重新读取并重新预览。');
          }else if(data.code==='preview_conflict'){
            setMessage('撤销未完成：预览之后记录又有变化，预览已失效。请重新读取并重新预览。');
          }else if(data.code==='version_conflict'){
            setMessage('撤销未完成：记录在预览之后又有修改，已拒绝覆盖。请重新读取并重新预览后再决定。');
          }else{
            setMessage(`撤销未完成：${data.message||data.code||'请重试'}。版本历史没有改变，可重新预览后再试。`);
          }
        }
      }
    });
    return unsubscribe;
  },[kind,id]);

  const requestPreview=(entry:KnowledgeHistoryEntry)=>{
    if(previewing||undoing)return;
    const target=entry.version;
    const operation_id=crypto.randomUUID();
    previewPending.current={operation_id,kind,id,target_version:target};
    setLastTarget(target);setPreviewing(true);setPreview(null);setMessage('');
    send('knowledge_undo_preview',operation_id,{target_version:target});
  };
  const previewAgain=()=>{
    if(previewing||undoing||lastTarget===null)return;
    const operation_id=crypto.randomUUID();
    previewPending.current={operation_id,kind,id,target_version:lastTarget};
    setPreviewing(true);setPreview(null);
    setMessage('正在重新读取记录并重新预览…');
    send('knowledge_undo_preview',operation_id,{target_version:lastTarget});
    requestHistory(page?.offset??offset);
  };
  const confirmUndo=()=>{
    if(!preview||previewing||undoing)return;
    const changes=preview.changes||[];
    const hasConflict=changes.some(change=>!!change.conflict);
    const token=preview.preview_token||'';
    if(preview.can_undo!==true||hasConflict||!token)return;
    const operation_id=crypto.randomUUID();
    undoPending.current={
      operation_id,kind,id,target_version:preview.target_version,
      expected_version:preview.current_version,preview_token:token,
    };
    const reason=undoReason.trim();
    setUndoing(true);
    setMessage(`正在撤销版本 v${preview.target_version} 的变更…`);
    send('knowledge_undo',operation_id,{
      target_version:preview.target_version,
      expected_version:preview.current_version,
      preview_token:token,
      ...(reason?{reason}:{}),
    });
  };

  const rows=page?.entries||[];
  const pageOffset=page?.offset??offset;
  const pageLimit=page?.limit||HISTORY_LIMIT;
  const total=page?.total||0;
  const pageCount=Math.max(1,Math.ceil(total/pageLimit));
  const currentPage=Math.min(pageCount,Math.floor(pageOffset/pageLimit)+1);
  const first=total?pageOffset+1:0;
  const last=Math.min(pageOffset+pageLimit,total);
  const changes=preview?.changes||[];
  const hasConflict=changes.some(change=>!!change.conflict);
  const canConfirm=!!preview&&preview.can_undo===true&&!hasConflict&&!!preview.preview_token&&!previewing&&!undoing;
  const success=message.startsWith('已撤销');

  return <section className="knowledge-history" id="knowledge-history" aria-label={`${TYPE_LABEL[kind]}版本历史`}>
    <header className="knowledge-history-head">
      <div>
        <h3 ref={heading} tabIndex={-1}>{TYPE_LABEL[kind]}版本历史</h3>
        <p>当前版本 v{page?.current_version??currentVersion??'未知'} · 每一条都是已提交历史；撤销会创建新版本，不会抹去已有版本。</p>
      </div>
      <div className="knowledge-history-actions">
        <button type="button" disabled={loading} onClick={()=>requestHistory(pageOffset)}>刷新</button>
        <button type="button" onClick={onClose}>收起版本历史</button>
      </div>
    </header>
    {message&&<p ref={success?successHeading:undefined} tabIndex={success?-1:undefined} className={success?'knowledge-history-success':'knowledge-history-message'} role={success?'status':'alert'}>{message}</p>}
    {error?<div className="workbench-error" role="alert"><p>{error}</p><button type="button" onClick={()=>requestHistory(pageOffset)}>重试</button></div>
      :loading&&!page?<p className="knowledge-history-message" role="status">正在读取版本历史…</p>
      :!rows.length?<p className="knowledge-history-message" role="status">尚无版本历史。创建、编辑、归档或撤销后会在这里记录。</p>
      :<>
        <p className="knowledge-history-message" role="status">{loading?'正在刷新版本历史…':`显示第 ${first}–${last} 条，共 ${total} 条。`}</p>
        {rows.map((entry,index)=><article className="knowledge-history-entry" key={`${entry.version}:${entry.action}:${entry.target_version??''}:${index}`}>
          <header>
            <h4>版本 v{entry.version} · {actionLabel(entry.action)}</h4>
            <span className="knowledge-history-meta">{timeLabel(entry.time)} · 操作者：{actorLabel(entry.actor)}</span>
          </header>
          <p className="knowledge-history-reason">理由：{reasonLabel(entry.reason)}</p>
          {typeof entry.target_version==='number'&&<p className="knowledge-history-note">已撤销版本 v{entry.target_version} 的变更</p>}
          <Collapsible title="查看变更前后">
            <div className="knowledge-history-snapshots">
              <Snapshot title="变更前" value={entry.before}/>
              <Snapshot title="变更后" value={entry.after}/>
            </div>
          </Collapsible>
          {entry.can_preview
            ?<div className="knowledge-history-actions"><button type="button" disabled={previewing||undoing} onClick={()=>requestPreview(entry)}>{previewing?'正在预览…':`预览撤销版本 v${entry.version} 的变更`}</button></div>
            :<p className="knowledge-history-note">该历史记录当前不可预览撤销，只能查看完整内容。</p>}
        </article>)}
        <nav className="workbench-paging" aria-label="版本历史分页">
          <button type="button" disabled={loading||pageOffset<=0} onClick={()=>requestHistory(Math.max(0,pageOffset-pageLimit))}>上一页</button>
          <span>第 {currentPage} / {pageCount} 页</span>
          <button type="button" disabled={loading||pageOffset+pageLimit>=total} onClick={()=>requestHistory(pageOffset+pageLimit)}>下一页</button>
        </nav>
      </>}
    {preview&&<section className="knowledge-history-preview" aria-label="撤销预览">
      <h4 ref={previewHeading} tabIndex={-1}>撤销版本 v{preview.target_version} 的变更 · 基于当前 v{preview.current_version}</h4>
      <p className="knowledge-history-warning">确认后会以当前版本为基础提交新版本，并保留对其他字段的后续修改。</p>
      {preview.code&&<p className="knowledge-history-warning" role="alert">不能撤销：{preview.message||CODE_LABEL[preview.code]||preview.code}</p>}
      {preview.can_undo!==true&&!preview.code&&<p className="knowledge-history-warning" role="alert">当前记录不能自动撤销。</p>}
      {hasConflict&&<p className="knowledge-history-warning" role="alert">有字段在此后已被修改，已标记为冲突；不会帮助覆盖这些修改，请重新读取并重新预览。</p>}
      {changes.length?<div className="knowledge-history-diff-wrap">
        <DataTable className="knowledge-history-diff">
          <thead><tr><th>字段</th><th>该历史变更前</th><th>该历史变更后</th><th>当前值</th><th>撤销后</th><th>冲突</th></tr></thead>
          <tbody>{changes.map((change,index)=><tr key={`${change.field}:${index}`} className={change.conflict?'conflict':''}>
            <th scope="row">{fieldLabel(change.field)}</th>
            <DiffCell field={change.field} value={change.before}/>
            <DiffCell field={change.field} value={change.after}/>
            <DiffCell field={change.field} value={change.current}/>
            <DiffCell field={change.field} value={change.proposed}/>
            <td className={change.conflict?'conflict-flag':''}>{change.conflict?'冲突：此后已修改':'—'}</td>
          </tr>)}</tbody>
        </DataTable>
      </div>:<p className="knowledge-history-note">预览没有返回字段差异；确认前请核对当前版本。</p>}
      {canConfirm?<>
        <div className="knowledge-history-form">
          <label>撤销理由（可选）<TextInput maxLength={500} value={undoReason} onChange={event=>setUndoReason(event.target.value)} placeholder="例如：恢复此前的判断"/></label>
        </div>
        <div className="knowledge-history-actions">
          <button type="button" disabled={undoing} onClick={confirmUndo}>{undoing?'正在撤销…':'确认撤销并创建新版本'}</button>
          <button type="button" disabled={undoing} onClick={()=>{setPreview(null);setLastTarget(null);setUndoReason('');setMessage('已取消本次撤销；没有写入任何版本。');}}>取消撤销</button>
        </div>
        <p className="knowledge-history-note">确认后会重新载入编辑器。未保存的表单修改会丢失。</p>
      </>:<div className="knowledge-history-actions">
        <button type="button" disabled={previewing||undoing||lastTarget===null} onClick={previewAgain}>{previewing?'正在重新预览…':'重新读取并重新预览'}</button>
        <button type="button" disabled={undoing} onClick={()=>{setPreview(null);setLastTarget(null);setUndoReason('');setMessage('已关闭预览；没有写入任何版本。');}}>关闭预览</button>
      </div>}
    </section>}
    {!preview&&!previewing&&lastTarget!==null&&<div className="knowledge-history-actions">
      <button type="button" disabled={undoing} onClick={previewAgain}>重新读取并重新预览版本 v{lastTarget}</button>
    </div>}
  </section>;
}
