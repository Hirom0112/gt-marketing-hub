// BulkBar (S12 W3) — the sticky dark dock that appears when families are
// multi-selected in the drill / show-all list. Two modes:
//   · default  → "<N> selected", ghost on-ink actions (Send nudge / Dismiss…),
//                a teal Capture-all primary, and a clear. Optionally a pre-send
//                partition line ("68 will send · 12 blocked by the gate") — the
//                VISIBLE-GATE trust signal (INV-3/4): the operator sees how many
//                of their batch the grounding gate will block BEFORE they send.
//   · dismiss  → a reason-picker rail (pills that hover red) replacing the dock.
//
// It composes the new Button `on-ink` (ghost-on-dark) + `flow` (teal) variants,
// so no raw colour lives here.

import { Button } from '../ui';

// The pre-send partition figures (the visible-gate trust signal).
export interface SendPartition {
  // How many of the selected batch will actually send (passed the gate).
  willSend: number;
  // How many were blocked by the grounding/safety gate.
  blocked: number;
}

interface BulkBarProps {
  // Number of selected families. 0 → the thin select-all rail (the dock absorbs
  // select-all); ≥1 → the dark action dock.
  count: number;
  // How many families are in view (for the "Select all N in view" rail).
  viewCount?: number;
  // Pre-formatted total recoverable-now of the selection (e.g. "$50,000"),
  // shown in the dock line ("N selected · $X recoverable").
  recoverableLabel?: string;
  // Select all rows in view (the rail's only action).
  onSelectAll?: () => void;
  // Default-mode actions.
  onNudge?: () => void;
  onCapture?: () => void;
  onClear?: () => void;
  // Open the dismiss reason-picker.
  onDismissStart?: () => void;
  // Whether the dismiss reason-picker is showing instead of the dock.
  pendingDismiss?: boolean;
  // The dismiss reasons to offer as pills.
  reasons?: readonly string[];
  // Pick a dismiss reason.
  onDismiss?: (reason: string) => void;
  // Back out of the dismiss picker.
  onCancelDismiss?: () => void;
  // Optional pre-send partition — when present, renders the gate trust line.
  partition?: SendPartition;
}

export default function BulkBar({
  count,
  viewCount = 0,
  recoverableLabel,
  onSelectAll,
  onNudge,
  onCapture,
  onClear,
  onDismissStart,
  pendingDismiss = false,
  reasons = [],
  onDismiss,
  onCancelDismiss,
  partition,
}: BulkBarProps): JSX.Element | null {
  // 0 selected → a thin select-all rail (the footer absorbs select-all). It only
  // appears when there are rows to select, never on a genuinely empty list.
  if (count === 0) {
    if (viewCount <= 0 || !onSelectAll) return null;
    return (
      <div className="bulk-rail" data-testid="bulk-rail">
        <button
          type="button"
          data-testid="bulk-rail-select-all"
          className="bulk-rail-btn"
          onClick={onSelectAll}
        >
          Select all {viewCount} in view
        </button>
      </div>
    );
  }

  if (pendingDismiss) {
    return (
      <div
        data-testid="bulk-bar-reasons"
        style={{
          display: 'flex',
          gap: 'var(--s-2)',
          flexWrap: 'wrap',
          alignItems: 'center',
          padding: 'var(--s-3) var(--s-4)',
          background: 'var(--surface-2)',
          borderTop: '1px solid var(--line-2)',
        }}
      >
        <span
          className="lab"
          style={{ alignSelf: 'center', marginRight: 'var(--s-1)' }}
        >
          dismiss {count} · pick a reason:
        </span>
        {reasons.map((r) => (
          <button
            key={r}
            type="button"
            data-testid={`bulk-reason-${r}`}
            onClick={() => onDismiss?.(r)}
            style={{
              border: '1px solid var(--line)',
              background: 'var(--surface)',
              fontSize: 11.5,
              fontWeight: 600,
              padding: '6px 11px',
              borderRadius: 'var(--r-pill)',
              color: 'var(--ink)',
              cursor: 'pointer',
              fontFamily: 'inherit',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = 'var(--signal)';
              e.currentTarget.style.color = 'var(--signal)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = 'var(--line)';
              e.currentTarget.style.color = 'var(--ink)';
            }}
          >
            {r}
          </button>
        ))}
        <button
          type="button"
          data-testid="bulk-reason-cancel"
          onClick={onCancelDismiss}
          style={{
            border: '1px solid transparent',
            background: 'transparent',
            color: 'var(--muted)',
            fontSize: 11.5,
            fontWeight: 600,
            padding: '6px 11px',
            cursor: 'pointer',
            fontFamily: 'inherit',
          }}
        >
          cancel
        </button>
      </div>
    );
  }

  return (
    <div
      data-testid="bulk-bar"
      style={{
        position: 'sticky',
        bottom: 0,
        display: 'flex',
        alignItems: 'center',
        flexWrap: 'wrap',
        gap: 'var(--s-3)',
        padding: 'var(--s-3) var(--s-4)',
        background: 'var(--ink)',
        color: 'var(--on-ink)',
        borderRadius: '0 0 var(--r-lg) var(--r-lg)',
      }}
    >
      <span
        className="mono"
        style={{ fontWeight: 700, fontSize: 13 }}
      >
        <b style={{ color: 'var(--on-ink-accent)' }}>{count}</b> selected
        {recoverableLabel ? (
          <span
            data-testid="bulk-bar-recoverable"
            style={{
              fontWeight: 600,
              color: 'rgba(255, 255, 255, 0.85)',
            }}
          >
            {' · '}
            {recoverableLabel} recoverable
          </span>
        ) : null}
      </span>
      {partition ? (
        <span
          className="mono"
          data-testid="bulk-bar-partition"
          style={{ fontSize: 11, color: 'rgba(255, 255, 255, 0.75)' }}
        >
          {partition.willSend} will send · {partition.blocked} blocked by the
          gate
        </span>
      ) : null}
      <Button variant="on-ink" onClick={onNudge} data-testid="bulk-nudge">
        Nudge
      </Button>
      <Button variant="flow" onClick={onCapture} data-testid="bulk-capture">
        Capture
      </Button>
      <Button
        variant="on-ink"
        onClick={onDismissStart}
        data-testid="bulk-dismiss-start"
      >
        Dismiss…
      </Button>
      <button
        type="button"
        data-testid="bulk-clear"
        onClick={onClear}
        style={{
          marginLeft: 'auto',
          background: 'none',
          border: 0,
          color: 'rgba(255, 255, 255, 0.7)',
          fontSize: 12,
          cursor: 'pointer',
          fontFamily: 'inherit',
        }}
      >
        clear
      </button>
    </div>
  );
}
