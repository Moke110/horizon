import {Button} from './gui/elements/Button';
import React,{useState} from 'react';

export function matchesEntryId(query:string,stored:string,prefix:string){
  const parse=(value:string)=>{
    const match=/^(?:([tqf])-)?([0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i.exec(value.trim());
    return match && (!match[1] || match[1].toLowerCase()===prefix) ? match[2].replaceAll('-','').toLowerCase() : null;
  };
  const body=parse(query);
  return !!body && body===parse(stored);
}

export function CopyEntryId({id}:{id:string}){
  const [status,setStatus]=useState('');
  return <><code>{id}</code> <Button type="button" onClick={async()=>{
    try{await navigator.clipboard.writeText(id);setStatus('已复制');}
    catch{setStatus(id);}
  }}>复制百科 ID</Button> <output>{status}</output></>;
}
