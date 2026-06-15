import type { ButtonHTMLAttributes } from 'react';
import type { LucideIcon } from 'lucide-react';

// A mono action button in three variants. `icon` takes a lucide-react icon
// component (rendered at a label-appropriate size). Hover/active states are
// handled in JS so the primitive stays self-contained (no global .btn CSS).
export type ButtonVariant = 'default' | 'primary' | 'signal';

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  icon?: LucideIcon;
}

function variantStyle(variant: ButtonVariant): {
  bg: string;
  fg: string;
  border: string;
} {
  switch (variant) {
    case 'primary':
      return { bg: 'var(--ink)', fg: 'var(--on-ink)', border: 'var(--ink)' };
    case 'signal':
      return {
        bg: 'var(--signal)',
        fg: 'var(--on-ink)',
        border: 'var(--signal)',
      };
    case 'default':
    default:
      return {
        bg: 'var(--surface)',
        fg: 'var(--ink)',
        border: 'var(--line)',
      };
  }
}

export function Button({
  variant = 'default',
  icon: Icon,
  children,
  style,
  disabled,
  ...rest
}: ButtonProps): JSX.Element {
  const v = variantStyle(variant);
  return (
    <button
      type="button"
      disabled={disabled}
      style={{
        fontFamily: 'var(--mono)',
        fontSize: '11.5px',
        display: 'inline-flex',
        alignItems: 'center',
        gap: 'var(--s-1)',
        padding: '7px 12px',
        borderRadius: 'var(--r-md)',
        border: `1px solid ${v.border}`,
        background: v.bg,
        color: v.fg,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        transition: 'border-color var(--dur) var(--ease), transform var(--dur) var(--ease)',
        ...style,
      }}
      onMouseDown={(e) => {
        if (!disabled) e.currentTarget.style.transform = 'translateY(1px)';
      }}
      onMouseUp={(e) => {
        e.currentTarget.style.transform = 'translateY(0)';
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.transform = 'translateY(0)';
        if (variant === 'default' && !disabled)
          e.currentTarget.style.borderColor = 'var(--line)';
      }}
      onMouseEnter={(e) => {
        if (variant === 'default' && !disabled)
          e.currentTarget.style.borderColor = 'var(--ink)';
      }}
      {...rest}
    >
      {Icon ? <Icon size={13} aria-hidden /> : null}
      {children}
    </button>
  );
}
