import { useEffect, useState } from 'react';
import ActionPanel from '../ActionPanel';
import DealView from '../DealView';
import FundingTracker from '../FundingTracker';
import LandingDashboard from '../LandingDashboard';
import PipelineBoard from '../PipelineBoard';
import SeamView from '../SeamView';
import WorkQueue from '../WorkQueue';
import { apiBaseUrl } from '../config';
import { Card } from '../ui';

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

type FamiliesState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready' };

export default function EnrollmentWorkspace(): JSX.Element {
  const [familiesState, setFamiliesState] = useState<FamiliesState>({
    status: 'loading',
  });
  const [selectedFamilyId, setSelectedFamilyId] = useState<string | null>(null);

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
        <DealView familyId={selectedFamilyId} />
        <div style={{ height: 1, background: 'var(--line)' }} aria-hidden />
        <ActionPanel familyId={selectedFamilyId} />
        <div style={{ height: 1, background: 'var(--line)' }} aria-hidden />
        <FundingTracker familyId={selectedFamilyId} />
      </Card>
    );
  }

  return (
    <section
      aria-label="Enrollment workspace"
      className="enrollment-workspace"
      style={{ display: 'grid', gap: 'var(--s-5)' }}
    >
      {/* KPI strip + seam ledger */}
      <LandingDashboard />

      {/* body: pipeline + recovery surfaces | live deal panel */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1.35fr) minmax(0, 1fr)',
          gap: 'var(--s-4)',
          alignItems: 'start',
        }}
      >
        <div style={{ display: 'grid', gap: 'var(--s-5)', minWidth: 0 }}>
          <PipelineBoard />
          <WorkQueue
            selectedFamilyId={selectedFamilyId ?? undefined}
            onSelectFamily={setSelectedFamilyId}
          />
          <SeamView />
        </div>

        {renderDealPanel()}
      </div>
    </section>
  );
}
