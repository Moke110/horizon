import {useId, useState, type ComponentPropsWithRef, type ComponentPropsWithoutRef, type ReactNode} from 'react';

export function FixedSelect({className, ...props}: ComponentPropsWithRef<'select'>) {
  return <select {...props} className={['gui-text', 'gui-select', className].filter(Boolean).join(' ')}/>;
}

export function Checkbox({children, className, ...props}: Omit<ComponentPropsWithRef<'input'>, 'type'> & {children: ReactNode}) {
  return <label className={['gui-checkbox', className].filter(Boolean).join(' ')}>
    <input {...props} type="checkbox"/>
    <span>{children}</span>
  </label>;
}

export function FilePicker({onChange, className, id, ...props}: Omit<ComponentPropsWithoutRef<'input'>, 'type'>) {
  const inputId = useId();
  const [fileName, setFileName] = useState('');
  return <div className={['gui-file-picker', className].filter(Boolean).join(' ')} aria-label={props['aria-label']}>
    <input {...props} id={id ?? inputId} type="file" onChange={event => {
      setFileName(Array.from(event.currentTarget.files ?? [], file => file.name).join('、'));
      onChange?.(event);
    }}/>
    <label htmlFor={id ?? inputId}>选择文件</label>
    <span title={fileName || undefined}>{fileName || '未选择文件'}</span>
  </div>;
}
