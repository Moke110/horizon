import React, {useEffect, useId, useLayoutEffect, useRef, useState} from 'react';
import {createPortal} from 'react-dom';

export function TextInput({className, ...props}: React.ComponentPropsWithoutRef<'input'>) {
  return <input {...props} className={['gui-text', className].filter(Boolean).join(' ')}/>;
}

export function MultilineInput({className, ...props}: React.ComponentPropsWithoutRef<'textarea'>) {
  return <textarea {...props} rows={1} className={['gui-text', 'gui-multiline', className].filter(Boolean).join(' ')}/>;
}

export function VariableList({label, items, onAdd, onEdit, onDelete, adding, editing, editor, onOutside, addDisabled}: {
  label: string;
  items: string[];
  onAdd: () => void;
  onEdit: (index: number) => void;
  onDelete: (index: number) => void;
  addDisabled?: boolean;
  adding?: boolean;
  editing?: number;
  editor?: React.ReactNode;
  onOutside?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const id = useId();
  const editingInline = adding || editing !== undefined;
  useEffect(() => {
    if (!onOutside) return;
    const check = (event: PointerEvent | FocusEvent) => {
      if (event.target instanceof Node && !ref.current?.contains(event.target)) onOutside();
    };
    document.addEventListener('pointerdown', check);
    document.addEventListener('focusin', check);
    return () => {
      document.removeEventListener('pointerdown', check);
      document.removeEventListener('focusin', check);
    };
  }, [onOutside]);
  return <div className="gui-variable-list" ref={ref}>
    <button type="button" className="gui-variable-toggle" aria-controls={id}
      aria-expanded={expanded} onClick={() => {
        if (expanded) onOutside?.();
        setExpanded(!expanded);
      }}>
      <span>{label}</span><span>{items.length ? `${items.length} 项` : '未添加'} <span aria-hidden="true">{expanded ? '⌄' : '›'}</span></span>
    </button>
    <ul id={id} hidden={!expanded} aria-label={label}>
      {items.map((item, index) => <li key={index}>
        {editing === index ? editor : <>
          <span>{item}</span>
          <div className="gui-variable-actions">
            <button type="button" aria-label={`编辑${label} ${item}`} disabled={editingInline} onClick={() => onEdit(index)}>编辑</button>
            <button type="button" aria-label={`删除${label} ${item}`} disabled={editingInline} onClick={() => onDelete(index)}>删除</button>
          </div>
        </>}
      </li>)}
      {adding && <li>{editor}</li>}
      <li><button type="button" aria-label={`新增${label}`} disabled={addDisabled || editingInline} onClick={onAdd}>+</button></li>
    </ul>
  </div>;
}

export function MainPanelDialog({title, onCancel, onSave, children}: {
  title: string;
  onCancel: () => void;
  onSave: () => void;
  children: React.ReactNode;
}) {
  const titleId = useId();
  const layer = useRef<HTMLDivElement>(null);
  const host = document.querySelector('main');
  useLayoutEffect(() => {
    if (!host) return;
    const opener = document.activeElement;
    const siblings = Array.from(host.children).filter(
      (child): child is HTMLElement => child instanceof HTMLElement && child !== layer.current && !child.inert,
    );
    siblings.forEach(child => {child.inert = true;});
    layer.current?.querySelector<HTMLElement>('select, input, button')?.focus();
    return () => {
      siblings.forEach(child => {child.inert = false;});
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, [host]);

  if (!host) return null;
  return createPortal(<div className="gui-dialog-layer" ref={layer}
    onKeyDown={event => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        onCancel();
      }
    }}>
    <form className="gui-dialog" role="dialog" aria-labelledby={titleId}
      onSubmit={event => {
        event.stopPropagation();
        event.preventDefault();
        onSave();
      }}>
      <h3 id={titleId}>{title}</h3>
      {children}
      <div className="gui-dialog-actions">
        <button type="button" onClick={onCancel}>取消</button>
        <button type="submit">保存</button>
      </div>
    </form>
  </div>, host);
}
