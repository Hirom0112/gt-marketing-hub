import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, CalendarDays, LayoutGrid } from 'lucide-react';
import ActionPanel from '../ActionPanel';
import DealView from '../DealView';
import FundingTracker from '../FundingTracker';
import LandingDashboard from '../LandingDashboard';
import PipelineBoard from '../PipelineBoard';
import SeamView from '../SeamView';
import WorkQueue from '../WorkQueue';
import CloseTipsPanel from '../enrollment/CloseTipsPanel';
import EnrollmentCalendar from '../enrollment/EnrollmentCalendar';
import NotesTimeline, {
  type NotesTimelineHandle,
} from '../enrollment/NotesTimeline';
import {
  type RecoverableRow,
  summarizeRecovery,
} from '../enrollment/recency';
import { apiBaseUrl } from '../config';
import { Card, WorkspaceToggle } from '../ui';

// S8 Wave 2 enrollment workspace — composes the (now re-skinned) real enrollment
// components into the reference's enrollment IA: a KPI strip up top (pipeline
// counts + CRM-seam ledger), then a two-column body — the pipeline board and the
// recovery work surfaces on the left, the live deal panel (deal view + AI action
// panel + funding/TEFA gate) on the right. Internals fetch real data; this
// container only places them and owns the focused-family SELECTION.
//
// Selection wiring (bug fix): the deal panel previously hardcoded familyId to a
// non-real id ('fam-a'), which 422s against the real API. We now load
// GET /families on mount, default the focus to the first real family_id (a
// UUID), and never mount the deal panel with a non-real id — the work queue
// drives selection via onSelectFamily.

// GET /families item — only the fields we read here (the API returns more).
interface FamilySummary {
  family_id: string;
  display_name: string;
}

// Which surface the left column shows: the pipeline board + capped work queue,
// or the full-width enrollment calendar. The calendar is reachable in ONE click
// (the toggle), not by scrolling a long page.
type LeftView = 'board' | 'calendar';

type FamiliesState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready' };

export default function EnrollmentWorkspace(): JSX.Element {
  const [familiesState, setFamiliesState] = useState<FamiliesState>({
    status: 'loading',
  });
  const [selectedFamilyId, setSelectedFamilyId] = useState<string | null>(null);
  // The left column defaults to the Board view; the toggle swaps to Calendar.
  const [leftView, setLeftView] = useState<LeftView>('board');
  // The /work-queue rows the situation bar summarizes (derived headline numbers,
  // INV-11 spirit — never hardcoded). The WorkQueue component still fetches its
  // own copy for the ranked list; this read feeds only the headline figures.
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

  // Pull the work-queue once for the situation-bar headline figures. Read-only
  // GET (INV-2); failures degrade silently to a hidden bar (the ranked list in
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
      <Card style={{ display: 'grid', gap: 'var(--s-4)', minWidth: 0 }}>
        <DealView familyId={selectedFamilyId} refreshKey={dealRefresh} />
        <div style={{ height: 1, background: 'var(--line)' }} aria-hidden />
        <ActionPanel
          familyId={selectedFamilyId}
          onActionApproved={handleActionApproved}
        />
        <div style={{ height: 1, background: 'var(--line)' }} aria-hidden />
        <CloseTipsPanel familyId={selectedFamilyId} />
        <div style={{ height: 1, background: 'var(--line)' }} aria-hidden />
        <NotesTimeline ref={notesRef} familyId={selectedFamilyId} />
        <div style={{ height: 1, background: 'var(--line)' }} aria-hidden />
        <FundingTracker familyId={selectedFamilyId} />
      </Card>
    );
  }

  const viewOptions = [
    { key: 'board' as const, label: 'Board', icon: LayoutGrid },
    { key: 'calendar' as const, label: 'Calendar', icon: CalendarDays },
  ];

  return (
    <section
      aria-label="Enrollment workspace"
      className="enrollment-workspace"
      style={{ display: 'grid', gap: 'var(--s-5)' }}
    >
      {/* Recovery-board front door: derived headline triage numbers, top of page */}
      {recoveryRows !== null && <SituationBar rows={recoveryRows} />}

      {/* KPI strip + seam ledger */}
      <LandingDashboard />

      {/* body: board ⇆ calendar surfaces | live deal panel */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1.35fr) minmax(0, 1fr)',
          gap: 'var(--s-4)',
          alignItems: 'start',
        }}
      >
        <div style={{ display: 'grid', gap: 'var(--s-5)', minWidth: 0 }}>
          {/* Left-column header: a one-click Board ⇆ Calendar toggle so the
              calendar is reachable without scrolling past the queue. */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'flex-end',
            }}
          >
            <div data-testid="enrollment-view-toggle">
              <WorkspaceToggle
                options={viewOptions}
                active={leftView}
                onSelect={setLeftView}
                ariaLabel="Enrollment view"
              />
            </div>
          </div>

          {leftView === 'board' ? (
            <>
              <PipelineBoard />
              <WorkQueue
                selectedFamilyId={selectedFamilyId ?? undefined}
                onSelectFamily={setSelectedFamilyId}
              />
              <SeamView />
            </>
          ) : (
            <EnrollmentCalendar
              selectedFamilyId={selectedFamilyId ?? undefined}
              onSelectFamily={setSelectedFamilyId}
            />
          )}
        </div>

        {renderDealPanel()}
      </div>
    </section>
  );
}

// The situation bar — a single row of derived headline numbers at the top of the
// Enrollment workspace, computed client-side from the fetched /work-queue rows
// (INV-11 spirit: nothing hardcoded). Reads as a triage headline:
// "⚠ N stalled · N overdue · $X recoverable this week".
function SituationBar({ rows }: { rows: readonly RecoverableRow[] }): JSX.Element {
  const { stalled, overdue, recoverableValue } = summarizeRecovery(rows);
  const dollars = recoverableValue.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  });
  return (
    <div data-testid="situation-bar">
      <Card
        className="situation-bar"
        style={{
          display: 'flex',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: 'var(--s-4)',
          background: 'var(--signal-wash)',
          borderColor: 'var(--signal)',
        }}
      >
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 'var(--s-2)',
            color: 'var(--signal-ink)',
            fontWeight: 600,
          }}
        >
          <AlertTriangle size={16} aria-hidden />
          <span className="mono" data-testid="situation-stalled">
            {stalled}
          </span>{' '}
          stalled
        </span>
        <span aria-hidden style={{ color: 'var(--line-strong)' }}>
          ·
        </span>
        <span
          style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--s-1)' }}
        >
          <span
            className="mono"
            data-testid="situation-overdue"
            style={{ fontWeight: 600, color: 'var(--signal-ink)' }}
          >
            {overdue}
          </span>{' '}
          overdue
        </span>
        <span aria-hidden style={{ color: 'var(--line-strong)' }}>
          ·
        </span>
        <span
          style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--s-1)' }}
        >
          <span
            className="mono"
            data-testid="situation-recoverable"
            style={{ fontWeight: 600, color: 'var(--gate-ink)' }}
          >
            {dollars}
          </span>{' '}
          recoverable this week
        </span>
      </Card>
    </div>
  );
}
