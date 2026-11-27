import React,{useEffect,useLayoutEffect,useRef,useState} from 'react';
import {TextInput} from './gui/elements/FormElements';

type Source = {
  id:string;
  name:string;
  description:string;
  configuration:'saved'|'environment'|'missing'|'public'|'optional';
  key_label:string|null;
  config_input_type:'password'|'url';
  monthly_limit?:number|null;
  enabled:boolean;
  connection:string;
};

function TavilyBudget({limit,disabled,send}:{
  limit:number|null|undefined;
  disabled:boolean;
  send:typeof window.horizon.send;
}) {
  return <form onSubmit={event=>{
    event.preventDefault();
    const fields=new FormData(event.currentTarget);
    const monthly_limit=Number(fields.get('monthly_limit'));
    if(fields.has('paygo_disabled')&&Number.isSafeInteger(monthly_limit)&&monthly_limit>0){
      send({action:'retriever_tavily_budget',paygo_disabled:true,monthly_limit});
    }
  }}>
    <h4>Tavily 免费预算</h4>
    <label><input type="checkbox" name="paygo_disabled" defaultChecked={limit!=null} required disabled={disabled}/>
      已在 Tavily 控制台关闭按量付费</label>
    <label>月度上限（credits）
      <TextInput name="monthly_limit" type="number" defaultValue={limit??''} min={1} max={Number.MAX_SAFE_INTEGER}
        step={1} required disabled={disabled}/>
    </label>
    <button disabled={disabled}>保存免费预算</button>
    {limit!=null&&<button type="button" disabled={disabled}
      onClick={()=>send({action:'retriever_tavily_budget',paygo_disabled:false,monthly_limit:null})}>清除免费预算确认</button>}
  </form>;
}

export function RetrieverPanel({send}:{send:typeof window.horizon.send}) {
  const [sources,setSources]=useState<Source[]>([]);
  const [error,setError]=useState('');
  const [testing,setTesting]=useState<string|null>(null);
  const panel=useRef<HTMLElement>(null);
  const scrollToTop=useRef(false);
  useLayoutEffect(()=>{
    if(scrollToTop.current){
      panel.current?.closest('main')?.scrollTo({top:0,behavior:'instant'});
      scrollToTop.current=false;
    }
  },[sources]);
  useEffect(()=>{
    const unsubscribe=window.horizon.subscribe(event=>{
      if(event.kind==='retriever_sources'){
        scrollToTop.current ||= event.data.added.length > 0;
        setSources(event.data.cards);
        if(event.data.completed_probe)setTesting(null);
        setError('');
      }
      if(event.kind==='retriever_error'){
        setTesting(null);
        setError(event.data);
      }
    });
    send({action:'retriever_sources'});
    return unsubscribe;
  },[send]);
  return <section ref={panel} aria-label="检索器" aria-busy={!!testing}>
    <h2>检索器</h2>
    <button type="button" disabled={!!testing} onClick={()=>{
      setTesting('all');
      send({action:'retriever_refresh'});
    }}>{testing==='all'?'正在刷新和测试…':'刷新并测试连接'}</button>
    {error&&<p role="alert">{error}</p>}
    {sources.map(source=><article key={source.id} className="card">
      <h3>{source.name}</h3>
      <p>{source.description}</p>
      <p role="status">{source.connection||'尚未测试连接'}</p>
      <p>{{
        missing:source.config_input_type==='url'?'尚未配置实例 URL':'尚未配置 API Key',
        saved:'使用本机保存的配置', environment:'使用环境变量中的配置',
        public:'无需 API Key', optional:'可选 API Key，当前使用公开额度',
      }[source.configuration]}</p>
      {source.key_label&&<form onSubmit={event=>{
        event.preventDefault();
        const form=event.currentTarget;
        const key=new FormData(form).get('key');
        send({action:'retriever_configure',source:source.id,key});
        form.reset();
      }}>
        <label>{source.key_label}<TextInput name="key" required type={source.config_input_type} maxLength={1024}
          autoComplete="off" disabled={!!testing}/></label>
        <button disabled={!!testing}>保存配置</button>
        {source.configuration==='saved'&&<button type="button" disabled={!!testing}
          onClick={()=>send({action:'retriever_configure',source:source.id,key:null})}>移除本机配置</button>}
      </form>}
      {source.id==='tavily'&&source.configuration==='saved'&&
        <TavilyBudget key={source.monthly_limit??'unset'} limit={source.monthly_limit} disabled={!!testing} send={send}/>}
      <label><input type="checkbox" checked={source.enabled} disabled={!!testing}
        onChange={event=>send({action:'retriever_toggle',source:source.id,enabled:event.target.checked})}/>启用</label>
      <button type="button" disabled={!!testing} onClick={()=>{
        setTesting(source.id);
        send({action:'retriever_test',source:source.id});
      }}>{testing===source.id?'正在测试…':'测试连接'}</button>
    </article>)}
  </section>;
}
