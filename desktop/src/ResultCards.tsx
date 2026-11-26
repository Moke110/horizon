import {useLayoutEffect,useRef,type ReactNode,type RefObject} from 'react';
import {Button} from './gui/elements/Button';

export function useMasonry(listRef:RefObject<HTMLUListElement|null>,enabled:boolean,content:unknown,sizeToContent=false) {
  useLayoutEffect(() => {
    const list = listRef.current;
    if (!enabled || !list) return;
    const items = Array.from(list.children) as HTMLLIElement[];
    const layout = () => {
      const style = getComputedStyle(list);
      const columns = Number(style.getPropertyValue('--term-columns'));
      const gap = parseFloat(style.columnGap);
      const inset = parseFloat(style.paddingInlineStart);
      const width = (list.clientWidth - inset - parseFloat(style.paddingInlineEnd) - (columns - 1) * gap) / columns;
      if (width <= 0) return;
      for (const item of items) item.style.width = width + 'px';
      const heights = items.map(item => item.getBoundingClientRect().height);
      const ends = Array(columns).fill(0);
      items.forEach((item,index) => {
        const column = ends.indexOf(Math.min(...ends));
        item.style.left = (inset + column * (width + gap)) + 'px';
        item.style.top = ends[column] + 'px';
        ends[column] += heights[index] + gap;
      });
      if (sizeToContent) list.style.height = Math.max(0,...ends) + "px";
    };
    layout();
    const observer = new ResizeObserver(layout);
    observer.observe(list);
    for (const item of items) observer.observe(item);
    return () => observer.disconnect();
  }, [listRef,enabled,content,sizeToContent]);
}

export function ResultCards({children,label}:{children:ReactNode;label:string}) {
  const ref=useRef<HTMLUListElement>(null);
  useMasonry(ref,true,children,true);
  return <div className="module-card-container"><ul ref={ref} className="module-card-list" aria-label={label}>{children}</ul></div>;
}

export function ResultCard({title,summary,status,entry,selected,onOpen}:{title:ReactNode;summary:string;status?:string;entry?:{validity?:string;tracked:boolean};selected:boolean;onOpen:()=>void}) {
  const description=entry && [
    entry.validity && ('状态：'+(entry.validity==='valid'?'有效':'存疑')),
    '追踪中：'+(entry.tracked?'是':'否')
  ].filter(Boolean).join('，');
  return <li><Button type="button" title={description} aria-description={description} data-tracked={entry?.tracked} data-result aria-current={selected||undefined} onClick={onOpen}>
    <span className="entry-heading"><span className="workbench-result-title">{title}</span>{entry?.validity&&<span aria-hidden="true" className="entry-validity" data-validity={entry.validity}/>}</span>
    {summary&&<span className="module-card-summary">{summary}</span>}
    {status&&<span className="workbench-result-meta"><span>{status}</span></span>}
  </Button></li>;
}
