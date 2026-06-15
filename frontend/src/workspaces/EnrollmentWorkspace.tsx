import ActionPanel from '../ActionPanel';
import DealView from '../DealView';
import FundingTracker from '../FundingTracker';
import LandingDashboard from '../LandingDashboard';
import PipelineBoard from '../PipelineBoard';
import SeamView from '../SeamView';
import WorkQueue from '../WorkQueue';

// S8 Wave 1 enrollment workspace — a thin container that places the existing
// real enrollment components in the new IA. Internals are unchanged this wave;
// only their placement moves (Wave 2 re-skins the components themselves).
export default function EnrollmentWorkspace(): JSX.Element {
  return (
    <section
      aria-label="Enrollment workspace"
      className="enrollment-workspace"
      style={{ display: 'grid', gap: 'var(--s-5)' }}
    >
      <LandingDashboard />
      <PipelineBoard />
      <DealView familyId="fam-a" />
      <WorkQueue />
      <ActionPanel familyId="fam-a" />
      <FundingTracker familyId="fam-a" />
      <SeamView />
    </section>
  );
}
