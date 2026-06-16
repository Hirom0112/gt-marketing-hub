// HistoryRow (S13 redesign) — the read-only ARCHIVE row. Deliberately shares NO
// grammar with the triage DrillRow: a recessed --surface-2 ground, more air, NO
// red anywhere, NO checkbox, NO rank, NO score, NO recoverable hero, NO bulk. Its
// absence of controls IS the "nothing to do here" cue. A 3px outcome rail
// (recovered→teal, dismissed→neutral) is the only color.
//
// Recovered: family + a detected-outcome chip (teal) + "recovered $X" (face
// value) + resolved date — NO reason/operator (the system detected it).
// Dismissed: family + a reason chip (neutral) + operator + date + a "set aside at
// {stage}" subline.

import { Chip } from '../ui';

export const HISTORY_GRID_RECOVERED = '1fr 150px 96px 84px';
export const HISTORY_GRID_DISMISSED = '1fr 150px 110px 84px';

export type RecoveredOutcome =
  | 'stage_advanced'
  | 'forms_cleared'
  | 'deposit_received';

// The detected-outcome label for a recovered family (BE's `recovered_outcome`).
const OUTCOME_LABEL: Record<RecoveredOutcome, string> = {
  stage_advanced: 'stage advanced',
  forms_cleared: 'forms cleared',
  deposit_received: 'deposit received',
};

// eslint-disable-next-line react-refresh/only-export-components
export function recoveredOutcomeLabel(
  outcome: string | null | undefined,
): string {
  if (outcome && outcome in OUTCOME_LABEL) {
    return OUTCOME_LABEL[outcome as RecoveredOutcome];
  }
  // Degrade gracefully — fall back to a generic "recovered" tag.
  return 'recovered';
}

interface HistoryRowProps {
  familyId: string;
  name: string;
  // 'recovered' | 'dismissed' — chooses the row grammar.
  kind: 'recovered' | 'dismissed';
  // Pre-formatted resolved/dismissed date (e.g. "Jun 13").
  when: string;
  // RECOVERED: the detected-outcome chip label + the face value ("$10,474").
  outcome?: string;
  amount?: string;
  // DISMISSED: the reason chip + the operator + the "set aside at {stage}" stage.
  reason?: string;
  operator?: string;
  stage?: string;
  active?: boolean;
  onSelect?: (familyId: string) => void;
}

export default function HistoryRow({
  familyId,
  name,
  kind,
  when,
  outcome,
  amount,
  reason,
  operator,
  stage,
  active = false,
  onSelect,
}: HistoryRowProps): JSX.Element {
  const grid =
    kind === 'recovered' ? HISTORY_GRID_RECOVERED : HISTORY_GRID_DISMISSED;
  return (
    <button
      type="button"
      data-testid={`history-row-${familyId}`}
      data-kind={kind}
      onClick={() => onSelect?.(familyId)}
      className={`history-row outcome-${kind}${active ? ' is-active' : ''}`}
      style={{ gridTemplateColumns: grid }}
    >
      {kind === 'recovered' ? (
        <>
          <span style={{ minWidth: 0 }}>
            <span className="history-name recovered">{name}</span>
          </span>
          <span
            style={{ display: 'flex', justifyContent: 'flex-start' }}
            data-testid={`history-outcome-${familyId}`}
          >
            <Chip tone="flow">{outcome}</Chip>
          </span>
          <span
            className="history-amount"
            data-testid={`history-amount-${familyId}`}
          >
            {amount}
          </span>
          <span className="history-when" data-testid={`history-when-${familyId}`}>
            {when}
          </span>
        </>
      ) : (
        <>
          <span style={{ minWidth: 0 }}>
            <span className="history-name dismissed">{name}</span>
            {stage ? (
              <small className="history-sub">set aside at {stage}</small>
            ) : null}
          </span>
          <span
            style={{ display: 'flex', justifyContent: 'flex-start' }}
            data-testid={`history-reason-${familyId}`}
          >
            <Chip tone="neutral">{reason}</Chip>
          </span>
          <span
            className="history-operator"
            data-testid={`history-operator-${familyId}`}
          >
            {operator}
          </span>
          <span className="history-when" data-testid={`history-when-${familyId}`}>
            {when}
          </span>
        </>
      )}
    </button>
  );
}
