import { useEffect, useMemo, useState } from 'react';
import { Pencil } from 'lucide-react';
import { Button } from '../ui';

// The daily Motivation banner (R6 / D-11). Local-only chrome — a quote is not
// applicant data, so it never touches the synthetic-data seam (INV-1 untouched):
// no backend, no fetch. ONE quote shows. It auto-rotates daily — the index is
// day-of-year % quotes.length, so the rotation is deterministic per calendar day
// (no clock-randomness flicker). The agent may click to EDIT the quote; a custom
// edit persists to localStorage['gtpulse.motd.<agentId>'] and, once stored, shows
// in place of the rotating default on every later mount. Quiet by design — the
// brief forbids it competing with operational info; it reuses the existing
// .dash-banner / .dash-banner-quote / .dash-banner-edit theme classes.

// A small synthetic, positive, short quote set. Encouraging, never operational.
const QUOTES: readonly string[] = [
  'Every call is a chance to change a family’s year.',
  'Small steps, taken daily, close big gaps.',
  'You don’t have to be perfect · just present.',
  'The next yes is closer than it feels.',
  'Follow up like it matters, because it does.',
  'Steady beats frantic. Work the list.',
  'Help one family today; the numbers follow.',
  'Your effort is the difference no dashboard shows.',
  'Listen first. The close takes care of itself.',
  'Momentum is built one conversation at a time.',
];

// The localStorage key for an agent's custom quote (D-11).
function storageKey(agentId: string): string {
  return `gtpulse.motd.${agentId}`;
}

// Day-of-year (1–366) for a date — drives the deterministic daily rotation.
function dayOfYear(date: Date): number {
  const start = Date.UTC(date.getFullYear(), 0, 0);
  const now = Date.UTC(date.getFullYear(), date.getMonth(), date.getDate());
  return Math.floor((now - start) / 86_400_000);
}

// The default rotating quote for "today" — UI chrome, so reading the wall clock
// at render is acceptable (D-11). Deterministic for the whole calendar day.
function quoteForToday(): string {
  const idx = dayOfYear(new Date()) % QUOTES.length;
  return QUOTES[idx] ?? QUOTES[0] ?? '';
}

// Read a stored custom quote for this agent (null when none / storage absent).
function loadCustom(agentId: string): string | null {
  try {
    return window.localStorage.getItem(storageKey(agentId));
  } catch {
    return null;
  }
}

interface MotivationBannerProps {
  agentId: string;
}

export default function MotivationBanner({
  agentId,
}: MotivationBannerProps): JSX.Element {
  const today = useMemo(() => quoteForToday(), []);
  // The shown quote: a stored custom edit if present, else today's rotation.
  const [quote, setQuote] = useState<string>(() => loadCustom(agentId) ?? today);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');

  // Re-resolve when the agent changes (a different seat → its own stored quote).
  useEffect(() => {
    setQuote(loadCustom(agentId) ?? today);
    setEditing(false);
  }, [agentId, today]);

  function startEdit(): void {
    setDraft(quote);
    setEditing(true);
  }

  function save(): void {
    const next = draft.trim();
    if (next === '') {
      setEditing(false);
      return;
    }
    try {
      window.localStorage.setItem(storageKey(agentId), next);
    } catch {
      // Persistence is best-effort chrome; a blocked storage never breaks the UI.
    }
    setQuote(next);
    setEditing(false);
  }

  return (
    <div className="dash-banner" data-testid="motivation-banner" role="note">
      {editing ? (
        <>
          <input
            className="dash-banner-edit"
            data-testid="motivation-input"
            aria-label="Edit motivational quote"
            value={draft}
            autoFocus
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') save();
              if (e.key === 'Escape') setEditing(false);
            }}
          />
          <Button data-testid="motivation-save" onClick={save}>
            Save
          </Button>
        </>
      ) : (
        <>
          <span className="dash-banner-quote" data-testid="motivation-quote">
            {quote}
          </span>
          <Button
            icon={Pencil}
            data-testid="motivation-edit"
            aria-label="Edit quote"
            title="Edit quote"
            onClick={startEdit}
            style={{
              background: 'transparent',
              border: 'none',
              color: 'var(--muted)',
              padding: 'var(--s-1)',
            }}
          />
        </>
      )}
    </div>
  );
}
