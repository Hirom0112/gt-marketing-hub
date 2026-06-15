import { useCallback, useState } from 'react';

// A tiny self-contained toast util (S12 W4). The recovery loop confirms every
// forwarded action ("12 nudges sent · 2 blocked by the gate") with a transient
// toast. Deliberately minimal — no portal, no animation lib (≤12-dep budget): a
// fixed-position stack rendered by the host, auto-expiring after a timeout.
//
// Each toast carries a headline `msg` and an optional `kick` subline (the mock's
// "batched · eval-gated" detail). A `tone` tints the left rail so a partial
// block (some families blocked by the gate) reads amber, never silently green.

export type ToastTone = 'flow' | 'gate' | 'signal';

export interface Toast {
  id: number;
  msg: string;
  kick?: string;
  tone: ToastTone;
}

export interface ToastApi {
  toasts: Toast[];
  push: (msg: string, opts?: { kick?: string; tone?: ToastTone }) => void;
  dismiss: (id: number) => void;
}

let nextId = 1;

// The toast queue hook. Returns the live list + a `push` the loop calls on every
// action response. Auto-expires each toast after ~3.5s (long enough to read a
// partition line). Pure of any DOM — the host below renders the list.
// eslint-disable-next-line react-refresh/only-export-components
export function useToasts(): ToastApi {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const dismiss = useCallback((id: number): void => {
    setToasts((list) => list.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (msg: string, opts?: { kick?: string; tone?: ToastTone }): void => {
      const id = nextId;
      nextId += 1;
      const toast: Toast = {
        id,
        msg,
        kick: opts?.kick,
        tone: opts?.tone ?? 'flow',
      };
      setToasts((list) => [...list, toast]);
      // Auto-expire (tests run fake-timer-free; the timeout is harmless there
      // because the host also renders a manual dismiss).
      setTimeout(() => dismiss(id), 3500);
    },
    [dismiss],
  );

  return { toasts, push, dismiss };
}

const RAIL: Record<ToastTone, string> = {
  flow: 'var(--flow)',
  gate: 'var(--gate)',
  signal: 'var(--signal)',
};

// The fixed bottom-right toast stack. Token-driven; a left rail carries the tone.
export function ToastHost({
  toasts,
  dismiss,
}: {
  toasts: readonly Toast[];
  dismiss: (id: number) => void;
}): JSX.Element {
  return (
    <div
      data-testid="toast-host"
      aria-live="polite"
      style={{
        position: 'fixed',
        right: 18,
        bottom: 18,
        zIndex: 50,
        display: 'flex',
        flexDirection: 'column',
        gap: 'var(--s-2)',
        maxWidth: 320,
      }}
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          role="status"
          data-testid="toast"
          onClick={() => dismiss(t.id)}
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 2,
            padding: 'var(--s-3) var(--s-4)',
            background: 'var(--ink)',
            color: 'var(--on-ink)',
            borderRadius: 'var(--r-md)',
            borderLeft: `3px solid ${RAIL[t.tone]}`,
            boxShadow: 'var(--shadow-md)',
            cursor: 'pointer',
            fontSize: 12.5,
          }}
        >
          <span data-testid="toast-msg" style={{ fontWeight: 600 }}>
            {t.msg}
          </span>
          {t.kick !== undefined && (
            <span
              className="mono"
              data-testid="toast-kick"
              style={{ fontSize: 11, color: 'rgba(255,255,255,0.7)' }}
            >
              {t.kick}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
