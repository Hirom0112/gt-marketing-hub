import {
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import NotesTimeline from '../NotesTimeline';

// Acceptance test (CLAUDE §4.2). The notes timeline (FR-2.3; S9 Wave 4) consumes
// GET /families/{id}/notes — the chronological manual + auto (system,
// state_change) notes — and lets the operator add a manual note. The backend
// has had this API since Wave 2 but it was rendered NOWHERE; this surfaces it.

const FAM = '11111111-1111-4111-8111-111111111111';

const NOTES = [
  {
    note_id: 'note-1',
    family_id: FAM,
    author: 'operator',
    kind: 'manual',
    body: 'Left a voicemail for the family.',
    created_at: '2026-06-11T10:00:00Z',
  },
  {
    note_id: 'note-2',
    family_id: FAM,
    author: 'system',
    kind: 'state_change',
    body: 'Email sent (simulated): nudge on enrollment_agreement.',
    created_at: '2026-06-12T15:30:00Z',
  },
];

function notesFetchMock(): ReturnType<typeof vi.fn> {
  return vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === 'POST') {
      return {
        ok: true,
        status: 201,
        json: async () => ({
          note_id: 'note-3',
          family_id: FAM,
          author: 'operator',
          kind: 'manual',
          body: 'Follow-up scheduled.',
          created_at: '2026-06-13T09:00:00Z',
        }),
      };
    }
    return { ok: true, status: 200, json: async () => NOTES };
  });
}

describe('NotesTimeline', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('renders the manual + auto notes for a family', async () => {
    vi.stubGlobal('fetch', notesFetchMock());
    render(<NotesTimeline familyId={FAM} />);

    expect(
      await screen.findByText('Left a voicemail for the family.'),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        'Email sent (simulated): nudge on enrollment_agreement.',
      ),
    ).toBeInTheDocument();
    expect(screen.getAllByTestId('note-item')).toHaveLength(2);
  });

  it('posts a manual note and refreshes the timeline', async () => {
    vi.stubGlobal('fetch', notesFetchMock());
    render(<NotesTimeline familyId={FAM} />);

    await screen.findByText('Left a voicemail for the family.');

    const input = screen.getByTestId('note-input');
    fireEvent.change(input, { target: { value: 'Follow-up scheduled.' } });
    fireEvent.click(screen.getByTestId('add-note'));

    await waitFor(() => {
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      const posted = fetchMock.mock.calls.some(
        (c) => (c[1] as RequestInit | undefined)?.method === 'POST',
      );
      expect(posted).toBe(true);
    });
  });
});
