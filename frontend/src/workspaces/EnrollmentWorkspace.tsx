import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, CalendarDays, List } from 'lucide-react';
import ActionPanel from '../ActionPanel';
import DealView from '../DealView';
import FundingTracker from '../FundingTracker';
import WorkQueue from '../WorkQueue';
import CloseTipsPanel from '../enrollment/CloseTipsPanel';
import EnrollmentCalendar from '../enrollment/EnrollmentCalendar';
import NotesTimeline, {
  type NotesTimelineHandle,
} from '../enrollment/NotesTimeline';
import { type RecoverableRow, summarizeRecovery } from '../enrollment/recency';
import { apiBaseUrl } from '../config';
import { Card, WorkspaceToggle } from '../ui';

// The operator page (S11 W2; A-17). This cockpit is the CATCH-AND-FORWARD layer,
// not the system of record: the operator's job is (1) see a stall the moment it
// happens, (2) capture context, (3) push to HubSpot. The page is therefore TWO
// primary surfaces only — a CALENDAR (find) on the LEFT and a family WORK-PANEL
// (act) on the RIGHT — under a thin SITUATION STRIP ("money on the table").
//
// The funnel scoreboard + the CRM-seam ledger moved to Leadership (A-17); the
// ranked work queue is DEMOTED to an optional "show everything regardless of
// date" list behind the calendar⇆all toggle. Nothing else competes for the
// operator's primary attention. Internals fetch real data; this container only
// places them and owns the focused-family SELECTION.

// GET /families item — only the fields we read here (the API returns more).
interface FamilySummary {
  family_id: string;
  display_name: string;
}

// The left surface: the default calendar (find by stall date), or the demoted
// "all families regardless of date" ranked list (the old work queue).
type LeftView = 'calendar' | 'all';

type FamiliesState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready' };

export default function EnrollmentWorkspace(): JSX.Element {
  const [familiesState, setFamiliesState] = useState<FamiliesState>({
    status: 'loading',
  });
  const [selectedFamilyId, setSelectedFamilyId] = useState<string | null>(null);
  // The left surface defaults to the CALENDAR (the primary "find"); the toggle
  // swaps to the demoted "all families" list.
  const [leftView, setLeftView] = useState<LeftView>('calendar');
  // The /work-queue rows the situation strip summarizes (derived headline
  // numbers, INV-11 spirit — never hardcoded). The WorkQueue component still
  // fetches its own copy for the ranked list; this read feeds only the headline.
  const [recoveryRows, setRecoveryRows] = useState<RecoverableRow[] | null>(
    null,
  );
  // Bumped after an approved follow-up to force the deal view to re-pull the
  // (now updated) recency; the notes timeline re-pulls via its imperative ref.
  const [dealRefresh, setDealRefresh] = useState(0);
  const notesRef = useRef<NotesTimelineHandle>(null);

  // The follow-up loop: on an approved AI action, the backend stamped recency +
  // wrote a deterministic auto-note. Re-pull both so they surface immediately.
  const handleActionApproved = useCallback((): void => {
    setDealRefresh((n) => n + 1);
    notesRef.current?.refresh();
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
          setFamiliesState({
            status: 'error',
            message: 'no families returned',
          });
          return;
        }
        // Default the focus to the first (real) family id once loaded.
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

  // Pull the work-queue once for the situation-strip headline figures. Read-only
  // GET (INV-2); failures degrade silently to a hidden strip (the ranked list in
  // WorkQueue surfaces its own error state).
  useEffect(() => {
    let cancelled = false;
    fetch(`${apiBaseUrl}/work-queue`)
      .then((res) => {
        if (!res.ok) throw new Error(`work-queue request failed: ${res.status}`);
        return res.json() as Promise<RecoverableRow[]>;
      })
      .then((rows) => {
        if (!cancelled) setRecoveryRows(rows);
      })
      .catch(() => {
        if (!cancelled) setRecoveryRows(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // The live deal panel — rendered ONLY once a real family id is selected, so
  // no child ever fetches against a non-real id (avoids the 422 entirely).
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
        <DealView familyId={selectedFamilyId} refreshKey={dealRefresh} />
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
    { key: 'all' as const, label: 'All families', icon: List },
  ];

  return (
    <section
      aria-label="Enrollment workspace"
      className="enrollment-workspace"
    >
      {/* Narrative beat 1 — "money on the table": a thin one-line strip at the
          very top, derived from the /work-queue rows (INV-11 spirit). */}
      {recoveryRows !== null && <SituationBar rows={recoveryRows} />}

      {/* The two primary surfaces: calendar (find) | family work-panel (act). */}
      <div className="operator-grid">
        <div className="operator-find">
          {/* The left-surface header: a title + a one-click Calendar ⇆ All
              toggle. The calendar is the default "find"; "All families" reveals
              the demoted ranked list regardless of date. */}
          <div className="find-head">
            <span className="lab find-head-title">Find a stall</span>
            <div data-testid="enrollment-view-toggle">
              <WorkspaceToggle
                options={viewOptions}
                active={leftView}
                onSelect={setLeftView}
                ariaLabel="Enrollment view"
              />
            </div>
          </div>

          {leftView === 'calendar' ? (
            <EnrollmentCalendar
              selectedFamilyId={selectedFamilyId ?? undefined}
              onSelectFamily={setSelectedFamilyId}
            />
          ) : (
            <WorkQueue
              selectedFamilyId={selectedFamilyId ?? undefined}
              onSelectFamily={setSelectedFamilyId}
            />
          )}
        </div>

        <div className="operator-act">{renderDealPanel()}</div>
      </div>
    </section>
  );
}

// The situation strip — a single line of derived headline numbers at the very
// top of the operator page, computed client-side from the fetched /work-queue
// rows (INV-11 spirit: nothing hardcoded). Reads as a triage headline:
// "⚠ N stalled · N overdue · $X recoverable this week". Per A-17, "stalled"
// EXCLUDES brand-new fresh leads (still inside the contact window).
function SituationBar({ rows }: { rows: readonly RecoverableRow[] }): JSX.Element {
  const { stalled, overdue, recoverableValue } = summarizeRecovery(rows);
  const dollars = recoverableValue.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  });
  return (
    <div data-testid="situation-bar">
      <Card className="situation-bar">
        <span className="situation-lead">
          <AlertTriangle size={16} aria-hidden />
          <span className="mono situation-figure" data-testid="situation-stalled">
            {stalled}
          </span>{' '}
          stalled
        </span>
        <span className="situation-dot" aria-hidden>
          ·
        </span>
        <span className="situation-item">
          <span
            className="mono situation-figure"
            data-testid="situation-overdue"
          >
            {overdue}
          </span>{' '}
          overdue
        </span>
        <span className="situation-dot" aria-hidden>
          ·
        </span>
        <span className="situation-item">
          <span
            className="mono situation-figure situation-money"
            data-testid="situation-recoverable"
          >
            {dollars}
          </span>{' '}
          recoverable this week
        </span>
      </Card>
    </div>
  );
}
