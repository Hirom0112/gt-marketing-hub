import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, CalendarDays, History, ListOrdered } from 'lucide-react';
import ActionPanel from '../ActionPanel';
import DealView from '../DealView';
import FundingTracker from '../FundingTracker';
import CloseTipsPanel from '../enrollment/CloseTipsPanel';
import EnrollmentCalendar, {
  type DrillBulk,
  type SortKey,
} from '../enrollment/EnrollmentCalendar';
import TriageList, { type TriageScope } from '../enrollment/TriageList';
import HistoryList from '../enrollment/HistoryList';
import NotesTimeline, {
  type NotesTimelineHandle,
} from '../enrollment/NotesTimeline';
import { ToastHost, useToasts } from '../enrollment/toast';
import { type RecoverableRow, summarizeRecovery } from '../enrollment/recency';
import { fmtUSD } from '../enrollment/format';
import { apiBaseUrl } from '../config';
import { Card, WorkspaceToggle } from '../ui';
import type { SendPartition } from '../enrollment/BulkBar';

// The operator page (S13 W1; A-17/A-19/A-20/A-22). CATCH-AND-FORWARD, not a
// system of record. The LEFT "find" surface has three views behind one toggle:
//   · Calendar (primary find) — families by stall date; busy days collapse to
//     heat. Tapping a cell/chip OPENS the triage list at Day scope for that day;
//     "This week" → Week scope; "Show all" → All scope.
//   · Triage (the OVERFLOW CONSOLE, A-22) — ONE scoped list (Day/Week/All over
//     the active set), recoverable-now ranked everywhere, bulk always attached.
//     The unscoped end of the calendar's drill, not a second surface.
//   · History — recovered/dismissed, EVICTED to its own clearly-separate view
//     (read-only audit/lookback, NO bulk).
// The RIGHT surface is the family work-panel (act). Under a thin SITUATION STRIP.
//
// The recovery LOOP: see a stall on the calendar → open the triage list at a
// scope → single OR bulk eval-gated action → recorded SERVER-SIDE (every action
// POSTs a real route; INV-2 no client write) → moved families re-pull and reflect
// their new recovery_state (and drop into History). The bulk routes, the shared
// selection Set, and the toasts all live HERE (one owner), passed down.

interface FamilySummary {
  family_id: string;
  display_name: string;
}

// The left "find" views. Triage carries a scope dial; History is its own view.
type LeftView = 'calendar' | 'triage' | 'history';

type FamiliesState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready' };

// The audited dismiss reasons (mock parity) — offered in the bulk + single
// reason pickers. A dismiss is the one new write on the audit spine (A-19).
const DISMISS_REASONS = [
  'Declined',
  'Bad fit',
  'Duplicate record',
  'Gone dark',
] as const;

// ── Bulk route responses (W2 contract) ───────────────────────────────────────
interface BulkNudgeResponse {
  batch_id: string;
  counts: { sent: number; blocked: number; capped: number };
  sent: Array<{ family_id: string; note_id: string }>;
  blocked: Array<{ family_id: string; failed_rules: string[] }>;
  capped: string[];
}
interface BulkSeedResponse {
  batch_id: string;
  counts: { captured: number };
  captured: Array<{ family_id: string; deal_id: string; seam_status: string }>;
}
interface BulkDismissResponse {
  batch_id: string;
  counts: { dismissed: number };
  dismissed: string[];
}

export default function EnrollmentWorkspace(): JSX.Element {
  const [familiesState, setFamiliesState] = useState<FamiliesState>({
    status: 'loading',
  });
  const [selectedFamilyId, setSelectedFamilyId] = useState<string | null>(null);
  const [leftView, setLeftView] = useState<LeftView>('calendar');
  // The triage list's scope dial (Day/Week/All) + the day it's windowed around.
  const [triageScope, setTriageScope] = useState<TriageScope>('all');
  const [triageAnchor, setTriageAnchor] = useState<string | undefined>(undefined);
  const [recoveryRows, setRecoveryRows] = useState<RecoverableRow[] | null>(
    null,
  );
  const [dealRefresh, setDealRefresh] = useState(0);
  // Bumped after a bulk write so the show-all list + situation strip re-pull the
  // queue and reflect the moved families' new recovery_state (no client write).
  const [queueRefresh, setQueueRefresh] = useState(0);
  const notesRef = useRef<NotesTimelineHandle>(null);
  const toasts = useToasts();

  // ── The shared selection Set + bulk picker state (one owner) ────────────────
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [pendingDismiss, setPendingDismiss] = useState(false);
  const [partition, setPartition] = useState<SendPartition | undefined>();
  const [sort, setSort] = useState<SortKey>('recoverable');

  const handleActionApproved = useCallback((): void => {
    setDealRefresh((n) => n + 1);
    setQueueRefresh((n) => n + 1);
    notesRef.current?.refresh();
  }, []);

  const reloadRows = useCallback((): void => {
    fetch(`${apiBaseUrl}/work-queue`)
      .then((res) => {
        if (!res.ok) throw new Error(`work-queue request failed: ${res.status}`);
        return res.json() as Promise<RecoverableRow[]>;
      })
      .then((rows) => setRecoveryRows(rows))
      .catch(() => setRecoveryRows((prev) => prev));
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch(`${apiBaseUrl}/families`)
      .then((res) => {
        if (!res.ok) throw new Error(`families request failed: ${res.status}`);
        return res.json() as Promise<FamilySummary[]>;
      })
      .then((families) => {
        if (cancelled) return;
        const first = families[0]?.family_id ?? null;
        if (first === null) {
          setFamiliesState({ status: 'error', message: 'no families returned' });
          return;
        }
        setSelectedFamilyId(first);
        setFamiliesState({ status: 'ready' });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : 'unknown error';
          setFamiliesState({ status: 'error', message });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // The situation-strip rows re-pull whenever a write moves families (queueRefresh).
  useEffect(() => {
    reloadRows();
  }, [reloadRows, queueRefresh]);

  // ── Selection helpers ───────────────────────────────────────────────────────
  const clearSel = useCallback((): void => {
    setSelected(new Set());
    setPendingDismiss(false);
    setPartition(undefined);
  }, []);

  const toggleSel = useCallback((familyId: string): void => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(familyId)) next.delete(familyId);
      else next.add(familyId);
      return next;
    });
  }, []);

  const selectAll = useCallback((ids: readonly string[]): void => {
    setSelected(new Set(ids.slice(0, 80)));
  }, []);

  // ── Bulk writes — every action POSTs a real route, then re-pulls (INV-2) ─────
  const afterBulk = useCallback((): void => {
    clearSel();
    setQueueRefresh((n) => n + 1);
    setDealRefresh((n) => n + 1);
  }, [clearSel]);

  const bulkNudge = useCallback((): void => {
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    fetch(`${apiBaseUrl}/ai/enrollment/bulk-nudge`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ family_ids: ids }),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`bulk-nudge failed: ${res.status}`);
        return res.json() as Promise<BulkNudgeResponse>;
      })
      .then((data) => {
        const { sent, blocked, capped } = data.counts;
        // Render the partition — blocked families are SHOWN, never hidden
        // (visible fail-closed gate; INV-3/4).
        toasts.push(`${sent} nudges sent`, {
          tone: blocked > 0 ? 'gate' : 'flow',
          kick:
            blocked > 0 || capped > 0
              ? `${blocked} blocked by the gate · ${capped} over the cap`
              : 'batched · eval-gated',
        });
        afterBulk();
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : 'unknown error';
        toasts.push('Bulk nudge failed', { tone: 'signal', kick: message });
      });
  }, [selected, toasts, afterBulk]);

  const bulkCapture = useCallback((): void => {
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    fetch(`${apiBaseUrl}/enrollment/families/bulk-seed`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ family_ids: ids }),
    })
      .then((res) => {
        if (!res.ok) throw new Error(`bulk-seed failed: ${res.status}`);
        return res.json() as Promise<BulkSeedResponse>;
      })
      .then((data) => {
        toasts.push(`${data.counts.captured} captured to HubSpot`, {
          kick: 'catch the wave · forward the batch',
        });
        afterBulk();
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : 'unknown error';
        toasts.push('Bulk capture failed', { tone: 'signal', kick: message });
      });
  }, [selected, toasts, afterBulk]);

  const bulkDismiss = useCallback(
    (reason: string): void => {
      const ids = Array.from(selected);
      if (ids.length === 0) return;
      fetch(`${apiBaseUrl}/enrollment/families/bulk-dismiss`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ family_ids: ids, reason }),
      })
        .then((res) => {
          if (!res.ok) throw new Error(`bulk-dismiss failed: ${res.status}`);
          return res.json() as Promise<BulkDismissResponse>;
        })
        .then((data) => {
          toasts.push(`${data.counts.dismissed} dismissed`, {
            tone: 'gate',
            kick: `moved to history · ${reason}`,
          });
          afterBulk();
        })
        .catch((err: unknown) => {
          const message = err instanceof Error ? err.message : 'unknown error';
          toasts.push('Bulk dismiss failed', { tone: 'signal', kick: message });
        });
    },
    [selected, toasts, afterBulk],
  );

  // A single-family dismiss (the work-panel "Dismiss this family") rides the same
  // bulk route with a one-id array (A-19 — one audited dismiss path).
  const dismissOne = useCallback(
    (familyId: string, reason: string): void => {
      fetch(`${apiBaseUrl}/enrollment/families/bulk-dismiss`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ family_ids: [familyId], reason }),
      })
        .then((res) => {
          if (!res.ok) throw new Error(`dismiss failed: ${res.status}`);
          return res.json() as Promise<BulkDismissResponse>;
        })
        .then(() => {
          toasts.push('Family dismissed', {
            tone: 'gate',
            kick: `moved to history · ${reason}`,
          });
          handleActionApproved();
        })
        .catch((err: unknown) => {
          const message = err instanceof Error ? err.message : 'unknown error';
          toasts.push('Dismiss failed', { tone: 'signal', kick: message });
        });
    },
    [toasts, handleActionApproved],
  );

  // The shared bulk wiring object (one source of truth for both surfaces).
  const bulk = useMemo<DrillBulk>(
    () => ({
      selected,
      onToggle: toggleSel,
      onSelectAll: selectAll,
      onClear: clearSel,
      onNudge: bulkNudge,
      onCapture: bulkCapture,
      onDismissStart: () => setPendingDismiss(true),
      pendingDismiss,
      reasons: DISMISS_REASONS,
      onDismiss: bulkDismiss,
      onCancelDismiss: () => setPendingDismiss(false),
      partition,
    }),
    [
      selected,
      toggleSel,
      selectAll,
      clearSel,
      bulkNudge,
      bulkCapture,
      pendingDismiss,
      bulkDismiss,
      partition,
    ],
  );

  function renderDealPanel(): JSX.Element {
    if (familiesState.status === 'error') {
      return (
        <Card>
          <p
            data-testid="enrollment-families-error"
            role="alert"
            style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
          >
            Could not load families: {familiesState.message}
          </p>
        </Card>
      );
    }
    if (selectedFamilyId === null) {
      return (
        <Card>
          <p data-testid="enrollment-deal-loading" className="lab">
            Loading deal panel…
          </p>
        </Card>
      );
    }
    return (
      <Card className="work-panel">
        <DealView
          familyId={selectedFamilyId}
          refreshKey={dealRefresh}
          dismissReasons={DISMISS_REASONS}
          onDismiss={dismissOne}
        />
        <div className="work-panel-rule" aria-hidden />
        <ActionPanel
          familyId={selectedFamilyId}
          onActionApproved={handleActionApproved}
        />
        <div className="work-panel-rule" aria-hidden />
        <CloseTipsPanel familyId={selectedFamilyId} />
        <div className="work-panel-rule" aria-hidden />
        <NotesTimeline ref={notesRef} familyId={selectedFamilyId} />
        <div className="work-panel-rule" aria-hidden />
        <FundingTracker familyId={selectedFamilyId} />
      </Card>
    );
  }

  const viewOptions = [
    { key: 'calendar' as const, label: 'Calendar', icon: CalendarDays },
    { key: 'triage' as const, label: 'Triage', icon: ListOrdered },
    { key: 'history' as const, label: 'History', icon: History },
  ];

  // Selecting a family from any surface drops any open bulk picker focus but
  // keeps the selection Set (so an operator can act on one while a batch is up).
  const selectFamily = useCallback((id: string): void => {
    setSelectedFamilyId(id);
  }, []);

  // The calendar opens the triage list at a scope (Day from a cell/chip, Week
  // from "This week", All from "Show all"). It switches the left view to triage
  // and seeds the scope dial; the dial then widens it without leaving.
  const openTriageScope = useCallback(
    (scope: TriageScope, anchorDate?: string): void => {
      setTriageScope(scope);
      setTriageAnchor(anchorDate);
      setLeftView('triage');
      clearSel();
    },
    [clearSel],
  );

  // The triage list's own scope dial — update the scope/anchor in place.
  const changeTriageScope = useCallback(
    (scope: TriageScope, anchorDate?: string): void => {
      setTriageScope(scope);
      setTriageAnchor(anchorDate);
    },
    [],
  );

  const findHeadTitle =
    leftView === 'calendar'
      ? 'Recovery calendar — the find surface, by stall date'
      : leftView === 'triage'
        ? 'Triage — recover in priority order, the order to attack the wave'
        : 'History — recovered & dismissed (read-only audit)';

  return (
    <section aria-label="Enrollment workspace" className="enrollment-workspace">
      {recoveryRows !== null && <SituationBar rows={recoveryRows} />}

      <div className="operator-grid">
        <div className="operator-find">
          <div className="find-head">
            <span className="lab find-head-title">{findHeadTitle}</span>
            <div data-testid="enrollment-view-toggle">
              <WorkspaceToggle
                options={viewOptions}
                active={leftView}
                onSelect={(v) => {
                  // A1 (Day-scope bug fix): opening Triage from the TOP TOGGLE
                  // defaults to ALL scope with no anchor — never an anchorless
                  // Day (which would window on the wall clock and show 0 rows).
                  // The calendar is the ONLY thing that opens Triage at Day/Week.
                  if (v === 'triage') {
                    setTriageScope('all');
                    setTriageAnchor(undefined);
                  }
                  setLeftView(v);
                  clearSel();
                }}
                ariaLabel="Enrollment view"
              />
            </div>
          </div>

          {leftView === 'calendar' && (
            <EnrollmentCalendar
              selectedFamilyId={selectedFamilyId ?? undefined}
              onSelectFamily={selectFamily}
              onOpenScope={openTriageScope}
            />
          )}
          {leftView === 'triage' && (
            <TriageList
              scope={triageScope}
              anchorDate={triageAnchor}
              onScopeChange={changeTriageScope}
              selectedFamilyId={selectedFamilyId ?? undefined}
              onSelectFamily={selectFamily}
              bulk={bulk}
              sort={sort}
              onSort={setSort}
              refreshKey={queueRefresh}
            />
          )}
          {leftView === 'history' && (
            <HistoryList
              selectedFamilyId={selectedFamilyId ?? undefined}
              onSelectFamily={selectFamily}
              refreshKey={queueRefresh}
            />
          )}
        </div>

        <div className="operator-act">{renderDealPanel()}</div>
      </div>

      <ToastHost toasts={toasts.toasts} dismiss={toasts.dismiss} />
    </section>
  );
}

// The situation strip — derived headline numbers from the /work-queue rows
// (INV-11 spirit: nothing hardcoded). "⚠ N active stalls · N gone overdue · $X
// at risk on the board". A-17: a fresh lead is still inside its contact window,
// so it is NOT a stall the loop is leaving on the table.
function SituationBar({ rows }: { rows: readonly RecoverableRow[] }): JSX.Element {
  const { stalled, overdue, recoverableValue } = summarizeRecovery(rows);
  return (
    <div data-testid="situation-bar">
      <Card className="situation-bar">
        <span className="situation-lead">
          <AlertTriangle size={16} aria-hidden />
          <span className="mono situation-figure" data-testid="situation-stalled">
            {stalled}
          </span>{' '}
          active stalls
        </span>
        <span className="situation-dot" aria-hidden>
          ·
        </span>
        <span className="situation-item">
          <span className="mono situation-figure" data-testid="situation-overdue">
            {overdue}
          </span>{' '}
          gone overdue
        </span>
        <span className="situation-dot" aria-hidden>
          ·
        </span>
        <span className="situation-item">
          <span
            className="mono situation-figure situation-money"
            data-testid="situation-recoverable"
          >
            {fmtUSD(recoverableValue)}
          </span>{' '}
          at risk on the board
        </span>
      </Card>
    </div>
  );
}
