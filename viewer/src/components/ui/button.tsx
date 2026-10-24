import * as React from 'react';
import { Button as ButtonPrimitive } from '@base-ui/react/button';
import { cva, type VariantProps } from 'class-variance-authority';

import { cn } from '@/lib/utils';

const buttonVariants = cva(
  'motion-tap inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-[10px] text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ds-brand-subtle focus-visible:ring-offset-1 focus-visible:ring-offset-ds-surface-white disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        // Primary: brand/default bg, brand/subtler border, brand/subtlest text
        default:
          'bg-ds-brand-default border-[1.5px] border-ds-brand-subtler text-ds-brand-subtlest hover:bg-ds-brand-subtle active:bg-ds-brand-darker',
        // Danger: danger/darker bg, danger/default border, white text
        destructive:
          'bg-ds-danger-darker border-[1px] border-ds-danger-default text-ds-surface-white hover:bg-ds-danger-default active:bg-ds-danger-darkest',
        // Destructive outline: quiet danger — transparent bg, danger text + subtle danger border, danger-tint hover. For guarded actions where the loud solid-red belongs on the confirm step.
        destructiveOutline:
          'bg-transparent border-[1px] border-ds-danger-subtler text-ds-danger-default hover:bg-ds-danger-subtlest hover:border-ds-danger-default',
        // Outline: transparent bg, border/subtlest border, text/default
        outline:
          'border-[1px] border-ds-border-subtlest bg-transparent text-ds-text-default hover:bg-ds-gray-subtlest',
        // Secondary: surface/white bg, border/subtlest border, text/default
        secondary:
          'bg-ds-surface-white border-[1px] border-ds-border-subtlest text-ds-text-default hover:bg-ds-gray-subtlest',
        // Ghost: no bg, no border, text/default
        ghost: 'bg-transparent text-ds-text-default hover:bg-ds-gray-subtlest',
        // Link: text/default with underline on hover
        link: 'bg-transparent text-ds-text-default underline-offset-4 hover:underline',
      },
      size: {
        default: 'h-9 px-4 py-2',
        sm: 'h-8 px-3 py-1.5 rounded-md text-xs',
        lg: 'h-11 px-4 py-3',
        icon: 'h-9 w-9',
        // Square icon button matched to `sm`, for icon controls that sit beside
        // a small button (h-9 `icon` next to an h-8 `sm` reads as misaligned).
        iconSm: 'h-8 w-8 rounded-md',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  }
);

export interface ButtonProps
  extends ButtonPrimitive.Props,
    VariantProps<typeof buttonVariants> {}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, nativeButton, render, ...props }, ref) => {
    // Base UI's Button defaults `nativeButton` to true and warns when `render`
    // supplies a non-<button> element (links, spans, divs). Disable native button
    // semantics for non-button renders unless the caller set it explicitly.
    const resolvedNativeButton =
      nativeButton ??
      (render != null &&
      !(React.isValidElement(render) && render.type === 'button')
        ? false
        : undefined);
    return (
      <ButtonPrimitive
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        nativeButton={resolvedNativeButton}
        render={render}
        {...props}
      />
    );
  }
);
Button.displayName = 'Button';

export { Button, buttonVariants };
