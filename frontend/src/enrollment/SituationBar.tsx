import { summarizeRecovery, type RecoverableRow } from './recency';
import { fmtUSD } from './format';

// SituationBar (M2) — a thin, reusable headline strip over a set of /work-queue
// rows. It is a PURE presentational fold of the rows it is handed (summarizeRecovery
// in recency.ts is the single math home; INV-11 spirit — nothing hardcoded), so
// the caller owns the read and the scoping.
//
// The REP variant ("my book") is fed the agent's OWNER-SCOPED /work-queue rows:
// because the caller reads through apiFetch (which attaches X-Demo-Agent-Id) and
// the backend clamps the agent to its own assigned_rep_id, these rows are already
// only that rep's families — the bar does no filtering of its own. M3's admin
// "team total" variant can reuse this same component with the team-wide rows.
//
// Three figures: # TO CONTACT (overdue + fresh, still un-actioned), # OVERDUE
// (aged past the follow-up threshold), and $ AT RISK (the recoverable value still
// in play). A rep reads their book at a glance before working the queue.

export type SituationVariant = 'rep' | 'team';

const VARIANT_META: Record<SituationVariant, { testId: string; label: string }> = {
  rep: { testId: 'rep-situation-bar', label: 'My book' },
  team: { testId: 'team-situation-bar', label: 'Team book' },
};

export default function SituationBar({
  rows,
  variant = 'rep',
}: {
  rows: readonly RecoverableRow[];
  variant?: SituationVariant;
}): JSX.Element {
  const { stalled, overdue, recoverableValue } = summarizeRecovery(rows);
  // "To contact" = the un-actioned, in-play set the rep must still reach — the
  // same `stalled` fold (fresh/overdue, not yet followed up or closed; A-17).
  const toContact = stalled;
  const meta = VARIANT_META[variant];

  return (
    <div className="situation-row">
      <div
        data-testid={meta.testId}
        className="situation-pill"
        aria-label={`${meta.label} — situation`}
      >
        <span className="lab situation-pill-eyebrow" aria-hidden>
          {meta.label}
        </span>
        <span className="situation-pill-divider" aria-hidden />
        <div className="situation-pill-cell">
          <span
            className="mono situation-pill-figure"
            data-testid="rep-situation-tocontact"
          >
            {toContact}
          </span>
          <span className="lab situation-pill-label">To contact</span>
        </div>
        <span className="situation-pill-divider" aria-hidden />
        <div className="situation-pill-cell">
          <span
            className="mono situation-pill-figure is-signal"
            data-testid="rep-situation-overdue"
          >
            {overdue}
          </span>
          <span className="lab situation-pill-label">Overdue</span>
        </div>
        <span className="situation-pill-divider" aria-hidden />
        <div className="situation-pill-cell">
          <span
            className="mono situation-pill-figure is-money"
            data-testid="rep-situation-atrisk"
          >
            {fmtUSD(recoverableValue)}
          </span>
          <span className="lab situation-pill-label">At risk</span>
        </div>
      </div>
    </div>
  );
}
