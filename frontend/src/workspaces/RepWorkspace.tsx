import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CalendarDays, ListOrdered } from 'lucide-react';
import ActionPanel from '../ActionPanel';
import DealView from '../DealView';
import FundingTracker from '../FundingTracker';
import CloseTipsPanel from '../enrollment/CloseTipsPanel';
import EnrollmentCalendar from '../enrollment/EnrollmentCalendar';
import TriageList, { type TriageScope } from '../enrollment/TriageList';
import NotesTimeline, {
  type NotesTimelineHandle,
} from '../enrollment/NotesTimeline';
import SituationBar from '../enrollment/SituationBar';
import { ToastHost, useToasts } from '../enrollment/toast';
import { type RecoverableRow } from '../enrollment/recency';
import { apiFetch } from '../config';
import { Card, WorkspaceToggle } from '../ui';
import type { SortKey } from '../enrollment/EnrollmentCalendar';
import type { DrillBulk } from '../enrollment/EnrollmentCalendar';
import type { SendPartition } from '../enrollment/BulkBar';

// RepWorkspace (M2; MULTI_AGENT_COCKPIT.md §4/§5, PLAN.md M2 R1/R2). The founder:
// "make sure the sales agent has only 1 dashboard where they can see everything
// needed … they do not need all that" (the big admin EnrollmentWorkspace).
//
// THE REP LENS — a SUBSET composition of the EXISTING enrollment machinery, not
// new machinery:
//   · TOP: the rep SituationBar — their BOOK (to-contact / overdue / $ at risk),
//     derived from the SAME owner-scoped /work-queue read the queue uses (one
//     read, one source of truth).
//   · LEFT: "My Queue" = the EXISTING TriageList (no new list — §4), with its
//     recency facets (overdue/fresh/working). It is owner-scoped AUTOMATICALLY:
//     apiFetch attaches the signed bearer token (agent_id in app_metadata) and
//     the backend clamps the agent to its
//     own assigned_rep_id (the IDOR defense, M1). No owner param, no client-side
//     security filter, no unscoped read.
//   · RIGHT: the close panel — the EXISTING DealView + ActionPanel + CloseTips +
//     NotesTimeline + FundingTracker stack, wired to the selected family exactly
//     as EnrollmentWorkspace wires it.
//
// ABSENT (admin-only, §5): the heat Calendar, the Students board, the Reconcile
// board / merge queue, Intake/assign, the per-agent roster, the Security tab.
//
// NO PARALLEL WRITE PATH (R2): every action rides the SAME gated route the admin
// ActionPanel uses — POST /proposals/{id}/decision (INV-2). The bulk handlers on
// the queue post the same owner-scoped routes the admin posts.

const DISMISS_REASONS = [
  'Declined',
  'Bad fit',
  'Duplicate record',
  'Gone dark',
] as const;

interface FamilySummary {
  family_id: string;
  display_name: string;
}

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

type FamiliesState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready' };

export default function RepWorkspace(): JSX.Element {
  const [familiesState, setFamiliesState] = useState<FamiliesState>({
    status: 'loading',
  });
  const [selectedFamilyId, setSelectedFamilyId] = useState<string | null>(null);
  // "My Queue" lives at ALL scope (the rep works their whole book ranked by
  // recoverable_now; the recency facets surface overdue/fresh/working — §4).
  const [triageScope, setTriageScope] = useState<TriageScope>('all');
  const [triageAnchor, setTriageAnchor] = useState<string | undefined>(
    undefined,
  );
  const [recoveryRows, setRecoveryRows] = useState<RecoverableRow[] | null>(
    null,
  );
  // The founder's ask: a calendar the rep can switch to a list. 'list' is the
  // default (the rep opens on their ranked queue); 'calendar' is the owner-scoped
  // recovery calendar (anchor=stall — "when they went quiet" = when to contact).
  const [queueView, setQueueView] = useState<'list' | 'calendar'>('list');
  const [dealRefresh, setDealRefresh] = useState(0);
  const [queueRefresh, setQueueRefresh] = useState(0);
  const [sort, setSort] = useState<SortKey>('likely');
  const notesRef = useRef<NotesTimelineHandle>(null);
  const toasts = useToasts();

  // Shared selection Set + bulk picker (one owner) — same pattern as the admin.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [pendingDismiss, setPendingDismiss] = useState(false);
  const [partition] = useState<SendPartition | undefined>();

  const handleActionApproved = useCallback((): void => {
    setDealRefresh((n) => n + 1);
    setQueueRefresh((n) => n + 1);
    notesRef.current?.refresh();
  }, []);

  // The SituationBar's rows = the SAME owner-scoped /work-queue read (one source
  // of truth). Server-scoped via apiFetch — no client-side security filter.
  const reloadRows = useCallback((): void => {
    apiFetch(`/work-queue`)
      .then((res) => {
        if (!res.ok)
          throw new Error(`work-queue request failed: ${res.status}`);
        return res.json() as Promise<RecoverableRow[]>;
      })
      .then((rows) => setRecoveryRows(rows))
      .catch(() => setRecoveryRows((prev) => prev));
  }, []);

  useEffect(() => {
    let cancelled = false;
    apiFetch(`/families`)
      .then((res) => {
        if (!res.ok) throw new Error(`families request failed: ${res.status}`);
        return res.json() as Promise<FamilySummary[]>;
      })
      .then((families) => {
        if (cancelled) return;
        const first = families[0]?.family_id ?? null;
        if (first === null) {
          setFamiliesState({
            status: 'error',
            message: 'no families returned',
          });
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

  useEffect(() => {
    reloadRows();
  }, [reloadRows, queueRefresh]);

  // ── Selection helpers (mirror the admin) ─────────────────────────────────────
  const clearSel = useCallback((): void => {
    setSelected(new Set());
    setPendingDismiss(false);
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

  const afterBulk = useCallback((): void => {
    clearSel();
    setQueueRefresh((n) => n + 1);
    setDealRefresh((n) => n + 1);
  }, [clearSel]);

  // Bulk writes — the SAME owner-scoped routes the admin posts (no parallel path).
  const bulkNudge = useCallback((): void => {
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    apiFetch(`/ai/enrollment/bulk-nudge`, {
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
    apiFetch(`/enrollment/families/bulk-seed`, {
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
      apiFetch(`/enrollment/families/bulk-dismiss`, {
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

  // A single-family dismiss rides the same audited route with a one-id array.
  const dismissOne = useCallback(
    (familyId: string, reason: string): void => {
      apiFetch(`/enrollment/families/bulk-dismiss`, {
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

  const selectFamily = useCallback((id: string): void => {
    setSelectedFamilyId(id);
  }, []);

  const changeTriageScope = useCallback(
    (next: TriageScope, anchorDate?: string): void => {
      setTriageScope(next);
      setTriageAnchor(anchorDate);
    },
    [],
  );

  // Opening a calendar cell/day jumps back to the LIST at that scope (the calendar
  // is a viewing aid; the rep works the list). Mirrors the admin openTriageScope.
  const openTriageScope = useCallback(
    (scope: TriageScope, anchorDate?: string): void => {
      setTriageScope(scope);
      setTriageAnchor(anchorDate);
      setQueueView('list');
    },
    [],
  );

  // The rep's list/calendar toggle options (their OWN switch — distinct from the
  // admin's multi-surface enrollment-view-toggle).
  const queueViewOptions = [
    { key: 'list' as const, label: 'List', icon: ListOrdered },
    { key: 'calendar' as const, label: 'Calendar', icon: CalendarDays },
  ];

  function renderDealPanel(): JSX.Element {
    if (familiesState.status === 'error') {
      return (
        <Card>
          <p
            data-testid="rep-families-error"
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
          <p data-testid="rep-deal-loading" className="lab">
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
          onChanged={handleActionApproved}
          variant="rep"
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

  return (
    <section
      aria-label="My enrollment queue"
      className="enrollment-workspace"
      data-testid="rep-workspace"
    >
      {recoveryRows !== null && (
        <SituationBar rows={recoveryRows} variant="rep" />
      )}

      <div className="operator-grid">
        <div className="operator-find">
          <div className="find-head">
            <span className="lab find-head-title">
              {queueView === 'calendar'
                ? 'My Calendar · when my families went quiet; the day to reach out'
                : 'My Queue · my families, ranked; work the overdue first'}
            </span>
            <div data-testid="rep-view-toggle">
              <WorkspaceToggle
                options={queueViewOptions}
                active={queueView}
                onSelect={setQueueView}
                ariaLabel="Queue view"
              />
            </div>
          </div>
          {queueView === 'calendar' ? (
            // The owner-scoped recovery calendar (anchor=stall — the default rep
            // flavor; apiFetch clamps the agent to its own book, M1). Opening a day
            // jumps back to the list at that scope.
            <EnrollmentCalendar
              selectedFamilyId={selectedFamilyId ?? undefined}
              onSelectFamily={selectFamily}
              onOpenScope={openTriageScope}
            />
          ) : (
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
        </div>

        <div className="operator-act">{renderDealPanel()}</div>
      </div>

      <ToastHost toasts={toasts.toasts} dismiss={toasts.dismiss} />
    </section>
  );
}
