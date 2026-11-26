import {Button} from './gui/elements/Button';
import React, {useRef, useState} from 'react';
import {TextInput} from './gui/elements/FormElements';
import {FixedSelect} from './gui/elements/ChoiceElements';
import './dictionary-search.css';

type Term = {card_id: string; title: string; aliases?: string[]};

export function DictionarySearch<S extends string>({query, setQuery, scope, changeScope, cards, selected, changeSelected, submit, scopeOptions, beforeQuery, extraFilter, placeholder='搜索术语和释义'}: {
  beforeQuery?: React.ReactNode; extraFilter?: React.ReactNode; scopeOptions?: Record<string,string>; placeholder?: string;
  query: string; setQuery: (value: string) => void; scope: S; changeScope: (value: S) => void;
  cards: Term[]; selected: string[]; changeSelected: (ids: string[]) => void; submit: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const [activeIndex, setActiveIndex] = useState(-1);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const needle = text.trim().toLocaleLowerCase();
  const matches = cards.filter(term => !selected.includes(term.card_id) &&
    [term.title, ...(term.aliases || [])].some(name => name.toLocaleLowerCase().includes(needle)));
  const choose = (term: Term) => {
    changeSelected([...selected, term.card_id]);
    setText('');
    setActiveIndex(-1);
    input.current?.focus();
    list.current?.scrollTo(0, 0);
  };
  return <form className="workbench-filters dictionary-search" role="search" aria-label="搜索导航栏"
    onSubmit={event => {event.preventDefault(); setOpen(false); submit();}}>
    <div><label htmlFor="dictionary-query">搜索</label>
      <div className="dictionary-query-row">
        {beforeQuery}
        <TextInput id="dictionary-query" type="search" value={query} onChange={e => setQuery(e.target.value)} placeholder={placeholder}/>
        <Button className="search-icon" type="submit" aria-label="搜索" title="搜索">
          <svg aria-hidden="true" width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></svg>
        </Button>
      </div>
    </div>
    <div className="dictionary-filter-row">
      <label>{scopeOptions ? '状态' : '范围'}<FixedSelect className="dictionary-scope" value={scope} onChange={e => {setOpen(false); changeScope(e.target.value as S);}}>
        {Object.entries(scopeOptions || {active:'当前有效',inactive:'归档与停用',pending:'待确认'}).map(([value,label])=><option key={value} value={value}>{label}</option>)}
      </FixedSelect></label>
      {extraFilter}
      <div className="term-picker" onBlur={e => {if (!e.currentTarget.contains(e.relatedTarget)) setOpen(false);}}>
        <label htmlFor="relation-term-input">关联术语</label>
        <div className="term-field" onClick={() => {if (scope !== 'pending') input.current?.focus();}}>
          {selected.map(id => {
            const title = cards.find(term => term.card_id === id)?.title ?? id;
            return <Button type="button" className="term-chip chosen" key={id} disabled={scope === 'pending'}
              aria-label={'移除' + title} onClick={() => changeSelected(selected.filter(value => value !== id))}>{title}<span aria-hidden="true">×</span></Button>;
          })}
          <TextInput ref={input} id="relation-term-input" role="combobox" autoComplete="off" aria-autocomplete="list"
            aria-expanded={open} aria-controls="relation-term-options" disabled={scope === 'pending'} value={text}
            placeholder="输入术语或同义词" aria-activedescendant={open && matches[activeIndex] ? 'term-opt-' + activeIndex : undefined}
            onFocus={() => setOpen(true)} onClick={() => setOpen(true)}
            onChange={e => {setText(e.target.value); setActiveIndex(-1); setOpen(true); list.current?.scrollTo(0, 0);}}
            onKeyDown={e => {
              if (e.nativeEvent.isComposing) return;
              if (e.key === 'Escape') {e.preventDefault(); setOpen(false); return;}
              if (e.key === 'Enter' && open) {e.preventDefault(); if (matches[activeIndex < 0 ? 0 : activeIndex]) choose(matches[activeIndex < 0 ? 0 : activeIndex]);}
              if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                e.preventDefault(); setOpen(true);
                const next = Math.min(matches.length - 1, Math.max(0, activeIndex + (e.key === 'ArrowDown' ? 1 : -1)));
                setActiveIndex(next);
                list.current?.children[next]?.scrollIntoView({block: 'nearest'});
              }
            }}/>
        </div>
        <div ref={list} className="term-options" id="relation-term-options" role="listbox" aria-label="候选术语" hidden={!open}>
          {matches.map((term, index) => <Button id={'term-opt-' + index} key={term.card_id} role="option" aria-selected={index === activeIndex}
            className="term-chip" type="button" tabIndex={-1} onMouseDown={e => e.preventDefault()} onClick={() => choose(term)}>{term.title}</Button>)}
          {!matches.length && <span role="status">没有匹配的术语</span>}
        </div>
      </div>
    </div>
  </form>;
}
