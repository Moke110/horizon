import {FixedSelect} from './gui/elements/ChoiceElements';
import React from 'react';

export type Convergence = 'resolved'|'unresolved'|'conflicted';

export const convergenceLabel = (value:Convergence,kind:'term'|'question') =>
  value==='resolved'?'已收敛':value==='unresolved'?'尚未收敛':kind==='term'?'释义矛盾':'重大矛盾';

export function ConvergenceSelect({value,kind,onChange}:{value:Convergence;kind:'term'|'question';onChange:(value:Convergence)=>void}) {
  const states:Convergence[]=['resolved','unresolved','conflicted'];
  return <label>收敛状态<FixedSelect value={value} onChange={event=>onChange(event.target.value as Convergence)}>
    {states.map(state=><option key={state} value={state}>{convergenceLabel(state,kind)}</option>)}
  </FixedSelect></label>;
}
