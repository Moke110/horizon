import type {ReactNode} from 'react';

export function ModuleHeading({icon,title,children}:{icon:ReactNode;title:string;children?:ReactNode}) {
  return <header className="dictionary-heading"><h1><span aria-hidden="true">{icon}</span>{title}</h1>{children}</header>;
}
