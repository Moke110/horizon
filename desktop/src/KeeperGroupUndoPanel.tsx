import {MultilineInput} from './gui/elements/FormElements';
import {DataTable} from './gui/elements/DataTable';
import React,{useEffect,useRef,useState} from 'react';
import type {KeeperApplied,KeeperGroupUndoPreview,KeeperProposal,KeeperUndoChange,KeeperUndoFieldChange,KeeperUndoReceipt} from './KeeperProposalPanel';

type UndoWork={operation_id:string;proposal_id:string;revision:number};
const KIND_LABEL:Record<string,string>={card:'知识卡片',theme:'命题',frontier:'关注问题'};
const ACTION_LABEL:Record<string,string>={restore:'恢复到批准前',archive:'撤销新建并归档',update:'恢复',delete:'删除'};
const FIELD_LABEL:Record<string,string>={
  title:'标题',content:'内容',aliases:'别名',sources:'来源',
  relations:'关联',questionable:'存疑',provenance:'来源性质',status:'状态',known:'是否计入已知',
  judgement:'判断',conditions:'适用条件',related_cards:'关联知识卡片',evidence:'证据',
  evidence_sufficiency:'证据充分性',question:'关注问题',scope:'研究范围',
  hard_constraints:'硬约束',must_include:'必须包含',exclude:'排除词',
};
const fieldLabel=(field:string):string=>FIELD_LABEL[field]
  ||FIELD_LABEL[field.split('.').pop()||'']||field;
const actionLabel=(action?:string):string=>action?(ACTION_LABEL[action]||action):'';
const CODE_LABEL:Record<string,string>={
  version_conflict:'记录在预览之后又有修改，请重新预览整组撤销',
  preview_conflict:'预览已失效，请重新预览整组撤销',
  stale_preview:'预览已过期，请重新预览整组撤销',
  undo_conflict:'撤销与后续修改冲突，已拒绝覆盖',
  operation_conflict:'操作编号已被使用，请重新预览整组撤销',
  invalid_target:'目标版本不可撤销，请重新预览',
  invalid_operation:'撤销请求无效，请重新预览',
  snapshot_conflict:'批准回执或历史快照不完整，不能撤销',
  invalid_relation:'撤销会留下无效的卡片或命题关联',
  already_undone:'该整组已经撤销',
  not_found:'记录或版本不存在',
};
const isRecord=(value:unknown):value is Record<string,unknown>=>!!value&&typeof value==='object'&&!Array.isArray(value);
const payloadMessage=(payload:Record<string,unknown>,fallback:string):string=>
  typeof payload.message==='string'&&payload.message?payload.message
    :typeof payload.code==='string'&&payload.code?payload.code
    :fallback;
const kindLabel=(kind:string)=>KIND_LABEL[kind]||kind;
const formatValue=(value:unknown):string=>{
  if(value===null||value===undefined)return '未记录';
  if(typeof value==='string')return value.trim()?value:'（空）';
  if(Array.isArray(value))return value.length?value.map(formatValue).join('、'):'（空）';
  if(isRecord(value)){
    const entries=Object.entries(value);
    if(!entries.length)return '（空）';
    return entries.map(([key,item])=>`${key}：${formatValue(item)}`).join('；');
  }
  return String(value);
};
const fieldChanges=(value:unknown):KeeperUndoFieldChange[]=>{
  if(!Array.isArray(value))return [];
  return value.flatMap(item=>{
    if(!isRecord(item))return [];
    const field=typeof item.field==='string'?item.field:'';
    if(!field)return [];
    return [{
      field,
      approved:item.approved,
      current:item.current,
      inverse:item.inverse,
      before:item.before,
      applied:item.applied,
      proposed:item.proposed,
      conflict:!!item.conflict,
    }];
  });
};
const undoChanges=(value:unknown):KeeperUndoChange[]=>{
  if(!Array.isArray(value))return [];
  return value.flatMap(item=>{
    if(!isRecord(item))return [];
    const kind=typeof item.kind==='string'?item.kind:'';
    const id=typeof item.id==='string'?item.id:'';
    if(!kind||!id)return [];
    return [{
      ...item,
      kind,id,
      target_version:typeof item.target_version==='number'?item.target_version:0,
      current_version:typeof item.current_version==='number'?item.current_version:0,
      conflict:!!item.conflict,
      field_changes:fieldChanges(item.field_changes),
    } as KeeperUndoChange];
  });
};
/** The K9 backend returns ordered `targets` with field-level approved/current/inverse changes. */
const undoChangesFromTargets=(value:unknown):KeeperUndoChange[]=>{
  if(!Array.isArray(value))return [];
  return value.flatMap(item=>{
    if(!isRecord(item))return [];
    const kind=typeof item.kind==='string'?item.kind:'';
    const id=typeof item.id==='string'?item.id:'';
    if(!kind||!id)return [];
    const changes=fieldChanges(item.changes);
    return [{
      kind,id,
      action:typeof item.action==='string'?item.action:undefined,
      approval_action:typeof item.approval_action==='string'?item.approval_action:undefined,
      target_version:typeof item.approved_version==='number'?item.approved_version:0,
      current_version:typeof item.current_version==='number'?item.current_version:0,
      proposed:item.projected,
      conflict:item.code==='version_conflict'||changes.some(change=>!!change.conflict),
      field_changes:changes,
    }];
  });
};
const appliedList=(value:unknown):KeeperApplied[]=>{
  if(!Array.isArray(value))return [];
  return value.flatMap(item=>{
    if(!isRecord(item))return [];
    const kind=typeof item.kind==='string'?item.kind:'';
    const id=typeof item.id==='string'?item.id:'';
    if(!kind||!id)return [];
    return [{
      kind,id,
      version:typeof item.version==='number'?item.version:0,
      status:typeof item.status==='string'?item.status:undefined,
    }];
  });
};
const normalizePreview=(payload:Record<string,unknown>,work:UndoWork):KeeperGroupUndoPreview=>{
  const expected=Array.isArray(payload.expected_versions)
    ?payload.expected_versions.filter(value=>typeof value==='number'&&Number.isInteger(value)&&value>0) as number[]
    :undefined;
  const flat=undoChanges(payload.changes);
  return {
    operation_id:work.operation_id,
    proposal_id:work.proposal_id,
    revision:work.revision,
    can_undo:payload.can_undo===true,
    code:typeof payload.code==='string'?payload.code:null,
    message:typeof payload.message==='string'?payload.message:undefined,
    preview_token:typeof payload.preview_token==='string'?payload.preview_token:undefined,
    ...(expected?{expected_versions:expected}:{}),
    changes:flat.length?flat:undoChangesFromTargets(payload.targets),
  };
};
export const keeperUndoPreviewFromPayload=normalizePreview;
const receiptFromPayload=(payload:Record<string,unknown>,work:UndoWork):KeeperUndoReceipt=>({
  ...payload,
  id:typeof payload.id==='string'?payload.id
    :typeof payload.undo_id==='string'?payload.undo_id
    :typeof payload.operation_id==='string'?payload.operation_id:null,
  revision:typeof payload.revision==='number'?payload.revision:work.revision,
  status:typeof payload.status==='string'?payload.status:'undone',
  proposal_id:typeof payload.proposal_id==='string'?payload.proposal_id:work.proposal_id,
  operation_id:work.operation_id,
  applied:appliedList(payload.applied),
});
const errorContext=(payload:Record<string,unknown>):Array<[string,string]>=>{
  const details=isRecord(payload.details)?payload.details:null;
  const result:Array<[string,string]>=[];
  const sources=[payload,details];
  const fields:Array<[string,string]>= [
    ['target_version','目标版本'],['current_version','当前版本'],
    ['expected_versions','预期版本'],['revision','提议修订'],
  ];
  for(const source of sources){
    if(!source)continue;
    for(const [key,label] of fields){
      const value=source[key];
      if(value===undefined||value===null)continue;
      if(result.some(([existing])=>existing===label))continue;
      result.push([label,Array.isArray(value)?value.map(formatValue).join('、'):formatValue(value)]);
    }
  }
  const targets=isRecord(details)&&Array.isArray(details.targets)?details.targets:[];
  for(const target of targets){
    if(!isRecord(target))continue;
    const id=typeof target.id==='string'?target.id:'';
    if(!id)continue;
    const approved=typeof target.approved_version==='number'?`v${target.approved_version}`:'未记录';
    const current=typeof target.current_version==='number'?`v${target.current_version}`:'未记录';
    result.push([`${kindLabel(typeof target.kind==='string'?target.kind:'')} ${id}`,`目标 ${approved} · 当前 ${current}`]);
  }
  return result;
};
/** Only a checked preview with a token, ordered versions, and no conflict may confirm. */
export const keeperUndoCanConfirm=(preview:KeeperGroupUndoPreview|null):boolean=>{
  if(!preview||preview.can_undo!==true)return false;
  if(!preview.preview_token)return false;
  if(!Array.isArray(preview.expected_versions)||preview.expected_versions.length===0)return false;
  return !(preview.changes||[]).some(change=>!!change.conflict);
};
export const keeperUndoPreviewCommand=(proposal:{proposal_id:string;revision:number},operation_id:string)=>(
  {
    action:'keeper_undo_preview' as const,
    schema_version:1 as const,
    operation_id,
    proposal_id:proposal.proposal_id,
    revision:proposal.revision,
  }
);
export const keeperUndoConfirmCommand=(
  proposal:{proposal_id:string;revision:number},
  preview:Pick<KeeperGroupUndoPreview,'expected_versions'|'preview_token'>,
  operation_id:string,
  reason:string,
)=>{
  const trimmed=reason.trim();
  return {
    action:'keeper_undo' as const,
    schema_version:1 as const,
    operation_id,
    proposal_id:proposal.proposal_id,
    revision:proposal.revision,
    expected_versions:Array.isArray(preview.expected_versions)?preview.expected_versions:[],
    preview_token:preview.preview_token||'',
    ...(trimmed?{reason:trimmed}:{}),
  };
};

function UndoCell({value}:{value:unknown}){
  return <td><p className="keeper-undo-value">{formatValue(value)}</p></td>;
}

function UndoFieldTable({changes}:{changes:KeeperUndoFieldChange[]}){
  return <div className="keeper-undo-diff-wrap">
    <DataTable className="keeper-undo-field-diff">
      <thead><tr><th>字段</th><th>已应用</th><th>当前</th><th>撤销后（基线）</th><th>冲突</th></tr></thead>
      <tbody>{changes.map((change,index)=><tr key={`${change.field}:${index}`} className={change.conflict?'conflict':''}>
        <th scope="row">{fieldLabel(change.field)}</th>
        <UndoCell value={change.approved??change.applied}/>
        <UndoCell value={change.current}/>
        <UndoCell value={change.inverse??change.proposed??change.before}/>
        <td className={change.conflict?'conflict-flag':''}>{change.conflict?'冲突：此后已修改':'—'}</td>
      </tr>)}</tbody>
    </DataTable>
  </div>;
}

function UndoTargetCards({changes}:{changes:KeeperUndoChange[]}){
  return <div className="keeper-undo-targets">
    {changes.map((change,index)=><section className={`keeper-undo-target${change.conflict?' conflict':''}`} key={`${change.kind}:${change.id}:${index}`}>
      <header className="keeper-undo-target-head">
        <strong>{kindLabel(change.kind)} · {change.id}</strong>
        <span>目标 v{change.target_version>0?change.target_version:'未记录'} · 当前 v{change.current_version>0?change.current_version:'未记录'}</span>
        {change.action&&<span>撤销动作：{actionLabel(change.action)}</span>}
        {change.approval_action&&<span>原批准：{actionLabel(change.approval_action)}</span>}
        {change.conflict&&<span className="conflict-flag">冲突：此后已修改</span>}
      </header>
      {change.field_changes?.length?<UndoFieldTable changes={change.field_changes}/>
        :<p className="keeper-value-empty">预检没有返回该对象的字段差异；确认前请核对当前版本。</p>}
    </section>)}
  </div>;
}

/**
 * Whole-group undo for one approved Keeper proposal. It opts in only on the
 * approved selection, previews through the persisted proposal/revision, and
 * keeps a separate persisted receipt from keeper_open's proposal.undo.
 */
export function KeeperGroupUndoPanel({
  proposal,receipt=null,onCommand,onUndone,
}:{
  proposal:KeeperProposal;
  receipt?:KeeperUndoReceipt|null;
  onCommand:(command:unknown)=>void;
  onUndone?:(receipt:KeeperUndoReceipt)=>void;
}){
  const [preview,setPreview]=useState<KeeperGroupUndoPreview|null>(null);
  const [previewing,setPreviewing]=useState(false);
  const [undoing,setUndoing]=useState(false);
  const [reason,setReason]=useState('');
  const validReason=new TextEncoder().encode(reason.trim()).length<=500;
  const [message,setMessage]=useState('');
  const [error,setError]=useState('');
  const [errorCode,setErrorCode]=useState('');
  const [context,setContext]=useState<Array<[string,string]>>([]);
  const [localReceipt,setLocalReceipt]=useState<KeeperUndoReceipt|null>(null);
  const previewWork=useRef<UndoWork|null>(null);
  const undoWork=useRef<UndoWork|null>(null);
  const previewHeading=useRef<HTMLHeadingElement>(null);
  const errorHeading=useRef<HTMLDivElement>(null);
  const command=useRef(onCommand);
  const undone=useRef(onUndone);
  command.current=onCommand;
  undone.current=onUndone;
  const revision=typeof proposal.revision==='number'?proposal.revision:0;
  const validRevision=Number.isInteger(revision)&&revision>0;
  const currentReceipt=localReceipt||(receipt?.status==='undone'?receipt:null);

  useEffect(()=>{
    previewWork.current=null;
    undoWork.current=null;
    setPreview(null);
    setPreviewing(false);
    setUndoing(false);
    setReason('');
    setMessage('');
    setError('');
    setErrorCode('');
    setContext([]);
    setLocalReceipt(null);
  },[proposal.proposal_id,proposal.revision]);
  useEffect(()=>{if(preview)previewHeading.current?.focus();},[preview]);
  useEffect(()=>{if(error)errorHeading.current?.focus();},[error]);
  useEffect(()=>{
    const unsubscribe=window.horizon.subscribe(event=>{
      const data=event?.data;
      if(!data||typeof data!=='object')return;
      const payload=data as Record<string,unknown>;
      const operation_id=typeof payload.operation_id==='string'?payload.operation_id:'';
      if(!operation_id)return;
      if(event.kind==='keeper_undo_preview'){
        const work=previewWork.current;
        if(!work||operation_id!==work.operation_id)return;
        previewWork.current=null;
        setPreviewing(false);
        if(payload.proposal_id!==work.proposal_id||payload.revision!==work.revision){
          setPreview(null);
          setError('整组撤销预览响应与当前提议或修订不匹配；未采用该预览，请重新预览。');
          setErrorCode('correlation_mismatch');
          setContext([]);
          setMessage('');
          return;
        }
        setPreview(normalizePreview(payload,work));
        setMessage('');
        setError('');
        setErrorCode('');
        setContext([]);
        return;
      }
      if(event.kind==='keeper_undo'){
        const work=undoWork.current;
        if(!work||work.operation_id!==operation_id)return;
        if(payload.proposal_id!==work.proposal_id||payload.revision!==work.revision){
          undoWork.current=null;
          setUndoing(false);
          setPreview(null);
          setError('整组撤销响应与当前提议或修订不匹配；未确认成功，请重新预览整组撤销。');
          setErrorCode('correlation_mismatch');
          setContext(errorContext(payload));
          setMessage('');
          return;
        }
        undoWork.current=null;
        setUndoing(false);
        const status=typeof payload.status==='string'?payload.status:'';
        if(status!=='undone'){
          setPreview(null);
          setError(`撤销响应状态为 ${status||'未知'}；未确认完成，请重新预览整组撤销。`);
          setErrorCode('invalid_status');
          setContext(errorContext(payload));
          return;
        }
        const result=receiptFromPayload(payload,work);
        setPreview(null);
        setLocalReceipt(result);
        setReason('');
        setMessage('已撤销整组；撤销回执由 keeper_open 的 proposal.undo 持久保存。');
        setError('');
        setErrorCode('');
        setContext([]);
        undone.current?.(result);
        return;
      }
      if(event.kind==='command_error'){
        if(previewWork.current?.operation_id===operation_id){
          previewWork.current=null;
          setPreviewing(false);
          setPreview(null);
          setError(payloadMessage(payload,'整组撤销预览未完成；请重新预览。'));
          setErrorCode(typeof payload.code==='string'?payload.code:'');
          setContext(errorContext(payload));
          setMessage('');
          return;
        }
        if(undoWork.current?.operation_id===operation_id){
          undoWork.current=null;
          setUndoing(false);
          setPreview(null);
          setError(payloadMessage(payload,'整组撤销未完成；请重新预览后再试。'));
          setErrorCode(typeof payload.code==='string'?payload.code:'');
          setContext(errorContext(payload));
          setMessage('');
        }
      }
    });
    return unsubscribe;
  },[proposal.proposal_id,revision]);

  if(proposal.status!=='approved')return null;
  if(currentReceipt){
    const applied=Array.isArray(currentReceipt.applied)?currentReceipt.applied:[];
    return <section className="keeper-group-undo" aria-label="整组撤销">
      <h3>整组撤销</h3>
      <p className="keeper-undo-message" role="status">整组撤销已完成；撤销回执由持久提议保存。</p>
      <dl className="keeper-undo-meta">
        <dt>撤销编号</dt><dd>{currentReceipt.id||currentReceipt.operation_id||'未记录'}</dd>
        <dt>状态</dt><dd>{currentReceipt.status==='undone'?'已撤销':currentReceipt.status||'已撤销'}</dd>
        <dt>提议修订</dt><dd>{typeof currentReceipt.revision==='number'?`v${currentReceipt.revision}`:'未记录'}</dd>
        {currentReceipt.reason&&<><dt>撤销理由</dt><dd>{currentReceipt.reason}</dd></>}
      </dl>
      <h4>已恢复对象</h4>
      {applied.length?<ul className="keeper-applied" aria-label="已恢复的知识对象">
        {applied.map((item,index)=><li key={`${item.kind}:${item.id}:${index}`}>
          <span>{kindLabel(item.kind)}</span>
          <span className="keeper-applied-id">{item.id}</span>
          <span>版本 v{item.version}</span>
        </li>)}
      </ul>:<p className="keeper-receipt-empty">撤销回执没有列出对象；请重新打开提议核对。</p>}
    </section>;
  }

  const requestPreview=()=>{
    if(!validRevision||previewing||undoing)return;
    const operation_id=crypto.randomUUID();
    previewWork.current={operation_id,proposal_id:proposal.proposal_id,revision};
    setPreviewing(true);
    setPreview(null);
    setMessage('正在读取整组撤销预检…');
    setError('');
    setErrorCode('');
    setContext([]);
    command.current(keeperUndoPreviewCommand({proposal_id:proposal.proposal_id,revision},operation_id));
  };
  const confirmUndo=()=>{
    if(!preview||previewing||undoing||!keeperUndoCanConfirm(preview)||!validReason)return;
    if(preview.proposal_id&&preview.proposal_id!==proposal.proposal_id)return;
    if(typeof preview.revision==='number'&&preview.revision!==revision)return;
    const operation_id=crypto.randomUUID();
    undoWork.current={operation_id,proposal_id:proposal.proposal_id,revision};
    setUndoing(true);
    setError('');
    setErrorCode('');
    setContext([]);
    setMessage('正在提交整组撤销…');
    command.current(keeperUndoConfirmCommand(
      {proposal_id:proposal.proposal_id,revision},preview,operation_id,reason));
  };
  const changes=preview?.changes||[];
  const hasConflict=changes.some(change=>!!change.conflict);
  const canConfirm=keeperUndoCanConfirm(preview);

  return <section className="keeper-group-undo" aria-label="整组撤销">
    <header className="keeper-group-undo-head">
      <div>
        <h3>整组撤销</h3>
        <p>先核对要恢复的内容。确认后，整组记录一起生成新版本。</p>
      </div>
    </header>
    {!validRevision&&<p className="keeper-decision-warning" role="alert">提议缺少有效修订，暂不能预览整组撤销；请重新打开提议核对。</p>}
    {error&&<div ref={errorHeading} tabIndex={-1} className="keeper-decision-error" role="alert">
      <p>{error}</p>
      {errorCode&&<p className="keeper-decision-note">错误代码：{errorCode}</p>}
      {context.length>0&&<dl className="keeper-undo-context">
        {context.map(([label,value])=><React.Fragment key={label}><dt>{label}</dt><dd>{value}</dd></React.Fragment>)}
      </dl>}
      <p className="keeper-decision-note">版本或预览已变化时必须重新预览；确认按钮不会沿用旧预览。</p>
    </div>}
    {message&&<p className="keeper-undo-message" role="status">{message}</p>}
    {!preview&&<div className="keeper-decision-actions">
      <button type="button" className="keeper-undo-preview" disabled={!validRevision||previewing||undoing} onClick={requestPreview}>
        {previewing?'正在预览整组撤销…':error?'重新预览整组撤销':'预览整组撤销'}
      </button>
    </div>}
    {preview&&<section className="keeper-undo-preview" aria-label="整组撤销预览">
      <h4 ref={previewHeading} tabIndex={-1}>整组撤销预览 · 提议修订 v{typeof preview.revision==='number'?preview.revision:revision}</h4>
      <p className="keeper-decision-note">目标版本由原批准写入。核对当前值与拟恢复值后再确认。</p>
      {preview.code&&<p className="keeper-decision-warning" role="alert">不能撤销：{CODE_LABEL[preview.code]||preview.message||preview.code}</p>}
      {preview.can_undo!==true&&!preview.code&&<p className="keeper-decision-warning" role="alert">预检返回不能撤销整组。</p>}
      {hasConflict&&<p className="keeper-decision-warning" role="alert">有对象在当前版本之后又被修改，已标记冲突；不会覆盖这些修改。请重新预览后再决定。</p>}
      {changes.length?(changes.some(change=>change.field_changes?.length)
        ?<UndoTargetCards changes={changes}/>
        :<div className="keeper-undo-diff-wrap">
          <DataTable className="keeper-undo-diff">
            <thead><tr><th>对象</th><th>目标版本</th><th>当前版本</th><th>变更前</th><th>已应用</th><th>当前</th><th>撤销后</th><th>冲突</th></tr></thead>
            <tbody>{changes.map((change,index)=><tr key={`${change.kind}:${change.id}:${index}`} className={change.conflict?'conflict':''}>
              <th scope="row"><span>{kindLabel(change.kind)}</span><span className="keeper-undo-id">{change.id}</span></th>
              <td>{typeof change.target_version==='number'&&change.target_version>0?`v${change.target_version}`:'未记录'}</td>
              <td>{typeof change.current_version==='number'&&change.current_version>0?`v${change.current_version}`:'未记录'}</td>
              <UndoCell value={change.before}/>
              <UndoCell value={change.applied}/>
              <UndoCell value={change.current}/>
              <UndoCell value={change.proposed}/>
              <td className={change.conflict?'conflict-flag':''}>{change.conflict?'冲突：此后已修改':'—'}</td>
            </tr>)}</tbody>
          </DataTable>
        </div>)
        :<p className="keeper-value-empty">预检没有返回对象差异；确认前请核对当前版本。</p>}
      {canConfirm?<>
        <label className="keeper-undo-reason">撤销理由（可选）
          <MultilineInput maxLength={500} value={reason} disabled={undoing} onChange={event=>setReason(event.target.value)} aria-invalid={!validReason} placeholder="例如：恢复合并前的卡片与关系"/>
        </label>
        {!validReason&&<p role="status">撤销理由最多 500 个 UTF-8 字节。</p>}
        <div className="keeper-decision-actions">
          <button type="button" className="keeper-undo-confirm" disabled={undoing||!validReason} onClick={confirmUndo}>
            {undoing?'正在撤销整组…':'确认整组撤销'}
          </button>
          <button type="button" disabled={undoing} onClick={()=>{setPreview(null);setReason('');setMessage('已关闭预览；没有写入任何版本。');}}>关闭预览</button>
        </div>
      </>:<div className="keeper-decision-actions">
        <button type="button" disabled={previewing||undoing} onClick={requestPreview}>
          {previewing?'正在重新预览…':'重新读取并预览整组撤销'}
        </button>
        <button type="button" disabled={undoing} onClick={()=>{setPreview(null);setReason('');setMessage('已关闭预览；没有写入任何版本。');}}>关闭预览</button>
      </div>}
    </section>}
  </section>;
}
