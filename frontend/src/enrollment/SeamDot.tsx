// SeamDot (S12 W3) — a 7px status dot for the HubSpot CRM seam, colour-coded by
// reconcile state: synced → --flow (green), unsynced → --gate (amber),
// conflict → --signal (red). One canonical map from seam status to the palette,
// so the deal panel and the leadership seam-health ledger read identically.

export type SeamStatus = 'synced' | 'unsynced' | 'conflict';

interface SeamDotProps {
  status: SeamStatus;
}

const SEAM_SOLID: Record<SeamStatus, string> = {
  synced: 'var(--flow)',
  unsynced: 'var(--gate)',
  conflict: 'var(--signal)',
};

export default function SeamDot({ status }: SeamDotProps): JSX.Element {
  return (
    <span
      data-testid="seam-dot"
      data-seam={status}
      aria-hidden
      style={{
        display: 'inline-block',
        width: 7,
        height: 7,
        borderRadius: '50%',
        background: SEAM_SOLID[status],
      }}
    />
  );
}
