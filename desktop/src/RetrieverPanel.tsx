import React,{useEffect,useLayoutEffect,useRef,useState} from 'react';
import {TextInput} from './gui/elements/FormElements';

type Source = {
  id:string;
  name:string;
  description:string;
  configuration:'saved'|'environment'|'missing';
  key_label:string;
  enabled:boolean;
  connection:string;
};

export function RetrieverPanel({send,onOpenHistory}:{send:typeof window.horizon.send;onOpenHistory:()=>void}) {
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
    <button type="button" onClick={onOpenHistory}>检索历史</button>
    <button type="button" disabled={!!testing} onClick={()=>{
      setTesting('all');
      send({action:'retriever_refresh'});
    }}>{testing==='all'?'正在刷新和测试…':'刷新并测试连接'}</button>
    {error&&<p role="alert">{error}</p>}
    {sources.map(source=><article key={source.id} className="card">
      <h3>{source.name}</h3>
      <p>{source.description}</p>
      <p role="status">{source.connection||'尚未测试连接'}</p>
      <p>{source.configuration==='missing'?'尚未配置 API Key':source.configuration==='saved'?'使用本机保存的 API Key':'使用环境变量中的 API Key'}</p>
      <form onSubmit={event=>{
        event.preventDefault();
        const form=event.currentTarget;
        const key=new FormData(form).get('key');
        send({action:'retriever_configure',source:source.id,key});
        form.reset();
      }}>
        <label>{source.key_label}<TextInput name="key" required type="password" autoComplete="off" disabled={!!testing}/></label>
        <button disabled={!!testing}>保存配置</button>
        {source.configuration==='saved'&&<button type="button" disabled={!!testing}
          onClick={()=>send({action:'retriever_configure',source:source.id,key:null})}>移除本机密钥</button>}
      </form>
      <label><input type="checkbox" checked={source.enabled} disabled={!!testing}
        onChange={event=>send({action:'retriever_toggle',source:source.id,enabled:event.target.checked})}/>启用</label>
      <button type="button" disabled={!!testing} onClick={()=>{
        setTesting(source.id);
        send({action:'retriever_test',source:source.id});
      }}>{testing===source.id?'正在测试…':'测试连接'}</button>
    </article>)}
  </section>;
}
