import React from 'react';

export function Collapsible({title, defaultOpen, className, children}: {
  title: React.ReactNode;
  defaultOpen?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  return <details className={['gui-collapsible', className].filter(Boolean).join(' ')} open={defaultOpen}>
    <summary><span>{title}</span></summary>
    {children}
  </details>;
}
