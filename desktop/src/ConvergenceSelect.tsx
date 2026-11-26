import {FixedSelect} from './gui/elements/ChoiceElements';
import React from 'react';

export type Convergence = 'resolved'|'unresolved'|'conflicted';

export const convergenceLabel = (value:Convergence,kind:'term'|'question') =>
  value==='resolved'?'有效 · 未追踪':value==='unresolved'?'有效 · 追踪中':'存疑 · 追踪中';

export function entryState(record?:{validity?:string;tracked?:boolean;convergence?:Convergence;questionable?:boolean}):Convergence {
  if (record?.validity) return record.validity==='questionable'?'conflicted':record.tracked?'unresolved':'resolved';
  return record?.convergence??(record?.questionable?'conflicted':'resolved');
}

export function stateFields(value:Convergence) {
  return {validity:value==='conflicted'?'questionable':'valid',tracked:value!=='resolved'};
}

export function ConvergenceSelect({value,onChange}:{value:Convergence;onChange:(value:Convergence)=>void}) {
  return <>
    <label>状态<FixedSelect value={value==='conflicted'?'questionable':'valid'} onChange={event=>onChange(event.target.value==='questionable'?'conflicted':'unresolved')}>
      <option value="valid">有效</option><option value="questionable">存疑</option>
    </FixedSelect></label>
    <label><input type="checkbox" checked={value!=='resolved'} disabled={value==='conflicted'}
      onChange={event=>onChange(event.target.checked?'unresolved':'resolved')}/>追踪中</label>
  </>;
}
