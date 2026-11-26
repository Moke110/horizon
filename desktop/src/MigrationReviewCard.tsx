import {Button} from './gui/elements/Button';
import React from 'react';

export type MigrationReview={id:number;raw:string;owner:string;deferred:boolean;
  candidates:{id:string;title:string;summary:string}[]};

export function MigrationReviewCard({review,decide}:{review:MigrationReview;decide:(target:string|null)=>void}){
  return <details key={String(review.deferred)} open={!review.deferred} className="child-result">
    <summary>待确认的旧引用 · {review.raw}</summary>
    <p>条目或记录 {review.owner}</p>
    {review.candidates.map(item=><section key={item.id}>
      <strong>{item.title}</strong> <code>{item.id}</code>
      <p>{item.summary}</p><Button type="button" onClick={()=>decide(item.id)}>关联到此条目</Button>
    </section>)}
    {!review.deferred&&<Button type="button" onClick={()=>decide(null)}>稍后处理</Button>}
  </details>;
}
