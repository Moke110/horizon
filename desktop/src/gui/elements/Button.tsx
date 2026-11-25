import type {ComponentPropsWithRef} from 'react';

type Props = ComponentPropsWithRef<'button'> & {variant?: 'standard' | 'primary' | 'archive'};

export function Button({variant = 'standard', className, ...props}: Props) {
  return <button {...props} className={['gui-button', 'gui-button-' + variant, className].filter(Boolean).join(' ')}/>;
}
