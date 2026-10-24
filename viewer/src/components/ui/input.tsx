import * as React from 'react';

import { cn } from '@/lib/utils';

const Input = React.forwardRef<HTMLInputElement, React.ComponentProps<'input'>>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(
          'flex h-9 w-full rounded-md border border-ds-border-subtlest bg-ds-surface-white px-3 py-1 text-base text-ds-text-darker shadow-sm transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-ds-text-default placeholder:text-ds-text-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ds-brand-subtle focus-visible:ring-offset-1 focus-visible:ring-offset-ds-surface-white disabled:cursor-not-allowed disabled:opacity-50 md:text-sm',
          className
        )}
        ref={ref}
        {...props}
      />
    );
  }
);
Input.displayName = 'Input';

export { Input };
