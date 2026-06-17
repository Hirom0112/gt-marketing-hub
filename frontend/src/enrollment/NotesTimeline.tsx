import {
  useCallback,
  useEffect,
  useImperativeHandle,
  useState,
  forwardRef,
} from 'react';
import { NotebookPen, Plus } from 'lucide-react';
import { apiFetch } from '../config';
import { Button, Card, Chip } from '../ui';

// Notes timeline (FR-2.3; S9 Wave 4). Consumes GET /families/{id}/notes — the
// chronological per-family timeline of manual (operator) + auto (system,
// state-change) notes — which the backend has had since Wave 2 but which was
// rendered NOWHERE. Operators can append a manual note (POST /families/{id}/notes).
// Native fetch only (≤12-dep budget). The deterministic core owns auto-notes
// (INV-2); this surface only reads the timeline and adds operator free text.
//
// A `refresh()` handle is exposed via ref so the deal panel can re-pull the
// timeline after an approved AI action — that approve writes a deterministic
// auto-note server-side (Wave 2), and refreshing surfaces it, closing the
// "tracks everything we do + auto-updates the notes" loop (vision item 6).

// One timeline entry — the backend core `Note` model (app/core/notes.py).
interface Note {
  note_id: string;
  family_id: string;
  author: 'operator' | 'system';
  kind: 'manual' | 'state_change';
  body: string;
  created_at: string;
}

interface NotesTimelineProps {
  familyId: string;
}

// The imperative handle the deal panel uses to re-pull after an approve.
export interface NotesTimelineHandle {
  refresh: () => void;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; notes: Note[] };

// Render a created_at ISO string as a compact, locale-stable label. Falls back
// to the raw string if it is not a parseable date (never throws / shows "null").
function formatWhen(iso: string): string {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return iso;
  return new Date(ms).toISOString().replace('T', ' ').slice(0, 16);
}

const NotesTimeline = forwardRef<NotesTimelineHandle, NotesTimelineProps>(
  function NotesTimeline({ familyId }, ref): JSX.Element {
    const [state, setState] = useState<LoadState>({ status: 'loading' });
    const [draft, setDraft] = useState('');
    const [submitting, setSubmitting] = useState(false);

    const load = useCallback((): (() => void) => {
      let cancelled = false;
      setState({ status: 'loading' });
      apiFetch(`/families/${familyId}/notes`)
        .then((res) => {
          if (!res.ok) throw new Error(`notes request failed: ${res.status}`);
          return res.json() as Promise<Note[]>;
        })
        .then((notes) => {
          if (!cancelled) setState({ status: 'ready', notes });
        })
        .catch((err: unknown) => {
          if (!cancelled) {
            const message = err instanceof Error ? err.message : 'unknown error';
            setState({ status: 'error', message });
          }
        });
      return () => {
        cancelled = true;
      };
    }, [familyId]);

    useEffect(() => {
      const cleanup = load();
      return cleanup;
    }, [load]);

    // Expose refresh() so the deal panel can re-pull after an approved action.
    // (load() returns a cancel fn used by the effect; refresh ignores it.)
    useImperativeHandle(
      ref,
      () => ({
        refresh: (): void => {
          load();
        },
      }),
      [load],
    );

    function submitNote(): void {
      const body = draft.trim();
      if (body === '' || submitting) return;
      setSubmitting(true);
      apiFetch(`/families/${familyId}/notes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ body }),
      })
        .then((res) => {
          if (!res.ok) throw new Error(`add-note request failed: ${res.status}`);
          return res.json() as Promise<Note>;
        })
        .then(() => {
          setDraft('');
          setSubmitting(false);
          load();
        })
        .catch((err: unknown) => {
          setSubmitting(false);
          const message = err instanceof Error ? err.message : 'unknown error';
          setState({ status: 'error', message });
        });
    }

    return (
      <section aria-label="Notes timeline" data-testid="notes-timeline">
        <div
          className="lab"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 'var(--s-1)',
            marginBottom: 'var(--s-2)',
          }}
        >
          <NotebookPen size={11} aria-hidden /> Notes — timeline (manual + auto)
        </div>

        {/* Add a manual note */}
        <div
          className="notes-compose"
          style={{ display: 'flex', gap: 'var(--s-2)', marginBottom: 'var(--s-3)' }}
        >
          <input
            data-testid="note-input"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Add a note to close the deal…"
            aria-label="Add a manual note"
            style={{
              flex: 1,
              minWidth: 0,
              fontFamily: 'var(--sans)',
              fontSize: 'var(--fs-sm)',
              color: 'var(--ink)',
              background: 'var(--surface)',
              border: '1px solid var(--line)',
              borderRadius: 'var(--r-sm)',
              padding: 'var(--s-2)',
            }}
          />
          <Button
            icon={Plus}
            data-testid="add-note"
            onClick={submitNote}
            disabled={submitting || draft.trim() === ''}
          >
            Add note
          </Button>
        </div>

        {state.status === 'loading' && (
          <p data-testid="notes-loading" className="lab">
            Loading notes…
          </p>
        )}

        {state.status === 'error' && (
          <p
            data-testid="notes-error"
            role="alert"
            style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
          >
            Could not load notes: {state.message}
          </p>
        )}

        {state.status === 'ready' &&
          (state.notes.length === 0 ? (
            <p data-testid="notes-empty" className="lab">
              No notes yet.
            </p>
          ) : (
            <ol
              className="notes-list scroll"
              style={{
                listStyle: 'none',
                margin: 0,
                padding: 0,
                display: 'grid',
                gap: 'var(--s-2)',
                maxHeight: 280,
                overflowY: 'auto',
              }}
            >
              {state.notes.map((note) => {
                const isAuto = note.author === 'system';
                return (
                  <li key={note.note_id} data-testid="note-item">
                    <Card pad>
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          gap: 'var(--s-2)',
                          marginBottom: 'var(--s-1)',
                        }}
                      >
                        <Chip tone={isAuto ? 'flow' : 'neutral'}>
                          {isAuto ? 'Auto' : 'Manual'}
                        </Chip>
                        <span className="lab">{formatWhen(note.created_at)}</span>
                      </div>
                      <p
                        data-testid="note-body"
                        style={{
                          fontSize: 'var(--fs-sm)',
                          color: 'var(--ink)',
                          whiteSpace: 'pre-wrap',
                          margin: 0,
                        }}
                      >
                        {note.body}
                      </p>
                    </Card>
                  </li>
                );
              })}
            </ol>
          ))}
      </section>
    );
  },
);

export default NotesTimeline;
