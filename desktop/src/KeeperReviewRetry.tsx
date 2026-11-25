import {Button} from './gui/elements/Button';
import React,{useEffect,useState} from 'react';

type Review={request_id?:string; review_id?:string; report_id?:string; report_version?:number; proposal_id?:string};

export function KeeperReviewRetry() {
  const send=window.horizon.send;
  const [reviews,setReviews]=useState<Review[]>([]);
  useEffect(()=>{
    const load=()=>send({action:'keeper_retryable_reviews'});
    const unsubscribe=window.horizon.subscribe(event=>{
      if(event.kind==='keeper_retryable_reviews')setReviews(event.data);
      if(['keeper_review','keeper_revision','keeper_retry_review'].includes(event.kind))load();
    });
    load();
    return unsubscribe;
  },[send]);
  if(!reviews.length)return null;
  return <section aria-label="Keeper 待重试审核">
    <h3>Keeper 待重试审核</h3>
    <ul>{reviews.map(review=><li key={review.review_id || review.request_id}>
      {review.review_id ? `提议 ${review.proposal_id?.slice(0,8)} 的复审` :
        `报道 ${review.report_id?.slice(0,8)} v${review.report_version} 的审核`}
      <Button type="button" onClick={()=>send({action:'keeper_retry_review',schema_version:1,
        operation_id:crypto.randomUUID(),...(review.review_id?{review_id:review.review_id}:{request_id:review.request_id})})}>
        重试审核
      </Button>
    </li>)}</ul>
  </section>;
}
