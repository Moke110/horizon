import React,{useEffect,useState} from 'react';

type CleanupReference={kind:string;id:string;title:string;effect:'removed'|'retained'|'blocked'};
type CleanupEntry={
  kind:string;
  id:string;
  title:string;
  version:number;
  blocked:boolean;
  references:CleanupReference[];
};
export type CleanupPlan={entries:CleanupEntry[]};

const labels:Record<string,string>={term:'术语',question:'百科问题',source:'资料库来源',
  frontier:'前沿',progress:'进展',original:'原文',retrieval:'检索记录',bulletin:'白板'};
const effects:Record<CleanupReference['effect'],string>={removed:'移除',retained:'保留',blocked:'引用仍在使用'};

export function TestTag({tags}:{tags?:string[]}){
  return tags?.includes('测试')?<span className="library-test-tag">测试</span>:null;
}

export function CleanupEntries({plan}:{plan:CleanupPlan}){
  if(!plan.entries.length)return <p>没有标记为测试的条目。</p>;
  return <ul className="cleanup-entries">{plan.entries.map(entry=><li key={entry.kind+':'+entry.id}>
    <div><strong>{labels[entry.kind]} · {entry.title}</strong> <TestTag tags={['测试']}/>
      <span>{entry.blocked?'保留，仍有其他条目引用':entry.kind==='term'?'将归档':'将删除'}</span>
    </div>
    {entry.references.length>0&&<ul aria-label="受影响的引用">
      {entry.references.map((reference,index)=><li key={reference.kind+':'+reference.id+':'+index}>
        {labels[reference.kind]} · {reference.title} · {effects[reference.effect]}
      </li>)}
    </ul>}
  </li>)}</ul>;
}

export function TestCleanupPanel({send,pending}:{send:(command:unknown)=>void;pending:boolean}){
  const [plan,setPlan]=useState<CleanupPlan|null>(null);
  useEffect(()=>{
    const unsubscribe=window.horizon.subscribe(event=>{
      if(event.kind==='test_cleanup_preview')setPlan(event.data);
    });
    window.horizon.send({action:'test_cleanup_preview'});
    return unsubscribe;
  },[]);
  return <section className="trace" aria-label="测试内容清理">
    <h2>清理测试内容</h2>
    <button type="button" onClick={()=>send({action:'test_cleanup_preview'})}>刷新清理预览</button>
    {plan&&<>
      <CleanupEntries plan={plan}/>
      <button type="button" disabled={pending||!plan.entries.some(entry=>!entry.blocked)} onClick={()=>send({
        action:'test_cleanup_request',operation_id:crypto.randomUUID(),
        selection:plan.entries.map(entry=>[entry.kind,entry.id]),
      })}>提交清理审核</button>
    </>}
  </section>;
}
