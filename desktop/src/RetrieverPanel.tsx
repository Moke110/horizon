import React,{useEffect,useState} from 'react';
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

export function RetrieverPanel({send}:{send:typeof window.horizon.send}) {
  const [sources,setSources]=useState<Source[]>([]);
  const [error,setError]=useState('');
  const [testing,setTesting]=useState<string|null>(null);
  useEffect(()=>{
    const unsubscribe=window.horizon.subscribe(event=>{
      if(event.kind==='retriever_sources'){
        setSources(event.data);
        setTesting(null);
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
  return <section aria-label="检索器">
    <h2>检索器</h2>
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
