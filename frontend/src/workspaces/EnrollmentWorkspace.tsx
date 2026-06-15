import ActionPanel from '../ActionPanel';
import DealView from '../DealView';
import FundingTracker from '../FundingTracker';
import LandingDashboard from '../LandingDashboard';
import PipelineBoard from '../PipelineBoard';
import SeamView from '../SeamView';
import WorkQueue from '../WorkQueue';
import { Card } from '../ui';

// S8 Wave 2 enrollment workspace — composes the (now re-skinned) real enrollment
// components into the reference's enrollment IA: a KPI strip up top (pipeline
// counts + CRM-seam ledger), then a two-column body — the pipeline board and the
// recovery work surfaces on the left, the live deal panel (deal view + AI action
// panel + funding/TEFA gate) on the right. Internals fetch real data; this
// container only places them. The focused family is fixed to fam-a this wave
// (selection wiring is a later concern; the panels already accept familyId).
const FOCUS_FAMILY = 'fam-a';

export default function EnrollmentWorkspace(): JSX.Element {
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
          <WorkQueue />
          <SeamView />
        </div>

        <Card style={{ display: 'grid', gap: 'var(--s-4)', minWidth: 0 }}>
          <DealView familyId={FOCUS_FAMILY} />
          <div style={{ height: 1, background: 'var(--line)' }} aria-hidden />
          <ActionPanel familyId={FOCUS_FAMILY} />
          <div style={{ height: 1, background: 'var(--line)' }} aria-hidden />
          <FundingTracker familyId={FOCUS_FAMILY} />
        </Card>
      </div>
    </section>
  );
}
