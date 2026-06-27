import { useEffect, useState } from 'react';
import { TrendingDown } from 'lucide-react';
import { apiFetch } from '../config';
import { dropOffPath } from './format';

// Aggregate apply-flow drop-off heatmap (S15 W2). Reads GET /drop-off/heatmap —
// exit counts per (step, form_key, field_key) cell, count-desc — and renders
// each as a row "step · form · field — N froze here" with a bar whose width is
// scaled to the busiest cell (visual weight by count). Aggregate only: *where*
// the cohort freezes, never *who* (INV-6). Theme tokens only, no raw hex.
//
// Empty buckets (the in-memory v1 fallback / no telemetry yet) ⇒ a graceful
// empty state, intentional not broken. Network error ⇒ a quiet error line.

interface DropOffBucket {
  step: string;
  form_key?: string | null;
  field_key?: string | null;
  count: number;
}

interface HeatmapResponse {
  buckets: DropOffBucket[];
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error' }
  | { status: 'ready'; buckets: DropOffBucket[] };

export default function DropOffHeatmap(): JSX.Element {
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    apiFetch(`/drop-off/heatmap`)
      .then((res) => {
        if (!res.ok) throw new Error(`heatmap request failed: ${res.status}`);
        return res.json() as Promise<HeatmapResponse>;
      })
      .then((data) => {
        if (!cancelled) {
          const buckets = Array.isArray(data?.buckets) ? data.buckets : [];
          setState({ status: 'ready', buckets });
        }
      })
      .catch(() => {
        if (!cancelled) setState({ status: 'error' });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section
      aria-label="Drop-off heatmap"
      data-testid="dropoff-heatmap"
      style={{ display: 'grid', gap: 'var(--s-3)' }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--s-2)',
          color: 'var(--muted)',
        }}
      >
        <TrendingDown size={14} aria-hidden />
        <span className="lab">
          Apply-flow drop-off · where the cohort froze (aggregate)
        </span>
      </div>

      {state.status === 'loading' && (
        <p data-testid="dropoff-heatmap-loading" className="lab">
          Loading drop-off…
        </p>
      )}

      {state.status === 'error' && (
        <p
          data-testid="dropoff-heatmap-error"
          role="alert"
          style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
        >
          Could not load drop-off data.
        </p>
      )}

      {state.status === 'ready' && state.buckets.length === 0 && (
        <p
          data-testid="dropoff-heatmap-empty"
          className="lab"
          style={{ color: 'var(--muted)' }}
        >
          No drop-off data yet (populates as applicants move through the apply
          flow).
        </p>
      )}

      {state.status === 'ready' && state.buckets.length > 0 && (
        <div
          data-testid="dropoff-heatmap-rows"
          style={{ display: 'grid', gap: 'var(--s-2)' }}
        >
          {(() => {
            const max = Math.max(...state.buckets.map((b) => b.count), 1);
            return state.buckets.map((b, i) => (
              <HeatmapRow
                key={`${b.step}-${b.form_key ?? ''}-${b.field_key ?? ''}-${i}`}
                bucket={b}
                intensity={b.count / max}
              />
            ));
          })()}
        </div>
      )}
    </section>
  );
}

// One drop-off cell row: the humanized step·form·field path, a bar scaled to the
// busiest cell, and the count. The bar tints with the signal accent (a drop-off
// is a leak) at an opacity ramped by intensity — channels stay in theme tokens.
function HeatmapRow({
  bucket,
  intensity,
}: {
  bucket: DropOffBucket;
  intensity: number;
}): JSX.Element {
  const i = Math.max(0, Math.min(1, intensity));
  const path = dropOffPath(bucket.step, bucket.form_key, bucket.field_key);
  return (
    <div
      data-testid="dropoff-heatmap-row"
      style={{
        display: 'grid',
        gridTemplateColumns: 'minmax(0, 1fr) auto',
        alignItems: 'center',
        gap: 'var(--s-3)',
        padding: '6px 9px',
        border: '1px solid var(--line)',
        borderRadius: 'var(--r-sm)',
        background: 'var(--surface-2)',
      }}
    >
      <div style={{ minWidth: 0 }}>
        <div
          data-testid="dropoff-heatmap-path"
          className="mono"
          style={{
            fontSize: 'var(--fs-sm)',
            color: 'var(--ink)',
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {path}
        </div>
        {/* The intensity bar · width AND tint opacity scale with the count, so
            the busiest cell reads loudest. `--i` feeds the inline calc; the
            colour channel is the signal token (no raw hex). */}
        <div
          aria-hidden
          style={{
            marginTop: 4,
            height: 6,
            borderRadius: 'var(--r-pill)',
            background: 'var(--line)',
            overflow: 'hidden',
          }}
        >
          <div
            data-testid="dropoff-heatmap-bar"
            style={
              {
                '--i': i,
                width: `${Math.max(6, i * 100)}%`,
                height: '100%',
                borderRadius: 'var(--r-pill)',
                background: 'var(--signal)',
                opacity: 'calc(0.45 + var(--i) * 0.55)',
              } as React.CSSProperties
            }
          />
        </div>
      </div>
      <span
        data-testid="dropoff-heatmap-count"
        className="mono"
        style={{
          fontSize: 'var(--fs-sm)',
          fontWeight: 700,
          color: 'var(--signal-ink)',
          whiteSpace: 'nowrap',
        }}
      >
        {bucket.count} froze here
      </span>
    </div>
  );
}
