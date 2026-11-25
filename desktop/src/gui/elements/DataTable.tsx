import React from 'react';

export function DataTable({className, ...props}: React.ComponentPropsWithoutRef<'table'>) {
  return <div className="gui-table-scroll">
    <table {...props} className={['gui-data-table', className].filter(Boolean).join(' ')}/>
  </div>;
}
