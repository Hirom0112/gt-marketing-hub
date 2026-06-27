import {
  CalendarDays,
  History,
  ListOrdered,
  ShieldCheck,
  Waves,
} from 'lucide-react';
import { Card } from '../ui';

// S14 Help — a short, static "How this works" panel. Real explanatory content,
// not decoration: the catch-and-forward model (A-17/A-22), what each of the
// three Enrollment find-views does, and the eval-gate fail-closed guarantee
// (INV-3/INV-4). No fetch, token-driven.

const VIEWS = [
  {
    icon: CalendarDays,
    name: 'Calendar',
    body: 'The primary find surface. Families laid out by stall date; busy days collapse to heat. Tap a day to drop into Triage scoped to it.',
  },
  {
    icon: ListOrdered,
    name: 'Triage',
    body: 'The overflow console. One scoped list (Day / Week / All), ranked by recoverable-now everywhere, with bulk actions always attached · the order to attack the wave.',
  },
  {
    icon: History,
    name: 'History',
    body: 'A read-only audit lookback of what was recovered or dismissed. No bulk, no red · its calm is the "nothing to do here" cue.',
  },
] as const;

export default function HelpWorkspace(): JSX.Element {
  return (
    <section
      aria-label="Help workspace"
      data-testid="help-workspace"
      style={{ display: 'grid', gap: 'var(--s-5)', maxWidth: 760 }}
    >
      <Card>
        <div className="help-block">
          <p className="help-block-head">
            <Waves size={16} aria-hidden style={{ color: 'var(--flow)' }} />
            Catch-and-forward, not a system of record
          </p>
          <ul className="help-model">
            <li>
              <span className="help-model-n mono">1</span> A wave of enrollment
              leads stalls. The cockpit surfaces who is still recoverable and
              what they are worth.
            </li>
            <li>
              <span className="help-model-n mono">2</span> An operator nudges or
              captures the warm ones · single or in bulk · and the action is
              recorded server-side.
            </li>
            <li>
              <span className="help-model-n mono">3</span> Captured families are
              forwarded to the CRM (HubSpot). This is the catch-and-forward
              layer; HubSpot stays the system of record.
            </li>
          </ul>
        </div>
      </Card>

      <Card>
        <p className="help-block-head">The three Enrollment views</p>
        <div className="help-views">
          {VIEWS.map(({ icon: Icon, name, body }) => (
            <div className="help-view" key={name}>
              <Icon
                size={18}
                aria-hidden
                style={{ color: 'var(--ink-soft)', flexShrink: 0 }}
              />
              <div>
                <p className="help-view-name">{name}</p>
                <p className="help-view-body">{body}</p>
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Card>
        <div className="help-block help-gate">
          <ShieldCheck
            size={18}
            aria-hidden
            style={{ color: 'var(--flow)', flexShrink: 0 }}
          />
          <div>
            <p className="help-block-head">The eval gate fails closed</p>
            <p className="help-view-body">
              Every AI action is gated by a tested eval. A generated message that
              fails the grounding and safety checks is blocked and shown · never
              quietly rewritten to pass. If an eval goes red, its action is
              visibly disabled in the UI. Fail closed, by design (INV-3/INV-4).
            </p>
          </div>
        </div>
      </Card>
    </section>
  );
}
