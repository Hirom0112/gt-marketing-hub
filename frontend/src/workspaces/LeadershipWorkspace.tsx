import EvalGate from '../EvalGate';
import Scoreboard from '../Scoreboard';

// S8 Wave 1 leadership workspace — houses the S7 scoreboard + eval gate.
// Components are mounted unchanged this wave (Wave 2 re-skins their internals).
export default function LeadershipWorkspace(): JSX.Element {
  return (
    <section
      aria-label="Leadership workspace"
      className="leadership-workspace"
      style={{ display: 'grid', gap: 'var(--s-5)' }}
    >
      <Scoreboard />
      <EvalGate />
    </section>
  );
}
