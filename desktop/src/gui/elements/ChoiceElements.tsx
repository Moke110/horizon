import {useEffect, useId, useState, type ComponentPropsWithRef, type ComponentPropsWithoutRef, type ReactNode} from 'react';

export function FixedSelect({className, ...props}: ComponentPropsWithRef<'select'>) {
  return <select {...props} className={['gui-text', 'gui-select', className].filter(Boolean).join(' ')}/>;
}

export function Checkbox({children, className, ...props}: Omit<ComponentPropsWithRef<'input'>, 'type'> & {children: ReactNode}) {
  return <label className={['gui-checkbox', className].filter(Boolean).join(' ')}>
    <input {...props} type="checkbox"/>
    <span>{children}</span>
  </label>;
}

function FileChoice({label, fileName, children, className}: {
  label?: string;
  fileName: string;
  children: ReactNode;
  className?: string;
}) {
  return <div className={['gui-file-picker', className].filter(Boolean).join(' ')} aria-label={label}>
    {children}
    <span title={fileName || undefined}>{fileName || '未选择文件'}</span>
  </div>;
}

export function FilePicker({onChange, className, id, ...props}: Omit<ComponentPropsWithoutRef<'input'>, 'type'>) {
  const inputId = useId();
  const [fileName, setFileName] = useState('');
  return <FileChoice className={className} label={props['aria-label']} fileName={fileName}>
    <input {...props} id={id ?? inputId} type="file" onChange={event => {
      setFileName(Array.from(event.currentTarget.files ?? [], file => file.name).join('、'));
      onChange?.(event);
    }}/>
    <label htmlFor={id ?? inputId}>选择文件</label>
  </FileChoice>;
}

export function NativeFilePicker({label, onBrowse}: {label: string; onBrowse: (pickerId: string) => void}) {
  const pickerId = useId();
  const [fileName, setFileName] = useState('');
  useEffect(() => window.horizon.subscribe(event => {
    if (event.kind === 'desktop_pdf_selected' && event.data.pickerId === pickerId) {
      setFileName(event.data.fileName);
    }
  }), [pickerId]);
  return <FileChoice label={label} fileName={fileName}>
    <button type="button" aria-label={label + '：选择文件'} onClick={() => onBrowse(pickerId)}>选择文件</button>
  </FileChoice>;
}
