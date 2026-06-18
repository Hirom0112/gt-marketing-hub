import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import StudentsTab from '../StudentsTab';

// Acceptance test (CLAUDE §4.2). The shared Students tab is a searchable roster of
// families. Search matches family AND student names (D-17 — student names indexed
// once from GET /students). Each row carries a derived status chip with the D-18
// precedence: Awaiting SIS > Closed > Working > No Contact. A row click selects the
// family. Read-only GETs (INV-2).

// Four families, one per chip state:
//   fam-await — paid but not in SIS  → Awaiting SIS (outranks everything)
//   fam-closed — recovery_state funded → Closed
//   fam-work  — contact_status followed_up → Working
//   fam-none  — stalled, never contacted → No Contact
const FAMILIES = [
  { family_id: 'fam-await', display_name: 'The Aw?ait Family' },
  { family_id: 'fam-closed', display_name: 'The Closed Family' },
  { family_id: 'fam-work', display_name: 'The Working Family' },
  { family_id: 'fam-none', display_name: 'The Nocontact Family' },
];

const WORK_QUEUE = [
  { family_id: 'fam-await', recovery_state: 'working', contact_status: 'followed_up' },
  { family_id: 'fam-closed', recovery_state: 'funded', contact_status: 'followed_up' },
  { family_id: 'fam-work', recovery_state: 'stalled', contact_status: 'followed_up' },
  { family_id: 'fam-none', recovery_state: 'stalled', contact_status: 'never' },
];

// fam-await is the PAID_NOT_IN_SIS cohort.
const SIS_BUCKETS = {
  buckets: [
    { bucket: 'paid_not_in_sis', count: 1, families: [{ family_id: 'fam-await' }] },
    { bucket: 'confirmed', count: 0, families: [] },
  ],
};

// /students board — fam-work's child is named "Mateo" so a student-name search hits.
const STUDENTS = {
  households: [
    {
      family_id: 'fam-work',
      students: [{ synthetic_first_name: 'Mateo', display_label: 'Working — Mateo · Grade 3' }],
    },
    {
      family_id: 'fam-none',
      students: [{ synthetic_first_name: 'Priya', display_label: 'Nocontact — Priya · Grade 1' }],
    },
  ],
};

function mockApi(): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (/\/families$/.test(url))
        return { ok: true, status: 200, json: async () => FAMILIES };
      if (/\/work-queue$/.test(url))
        return { ok: true, status: 200, json: async () => WORK_QUEUE };
      if (/\/enrollment\/sis-buckets$/.test(url))
        return { ok: true, status: 200, json: async () => SIS_BUCKETS };
      if (/\/students$/.test(url))
        return { ok: true, status: 200, json: async () => STUDENTS };
      throw new Error(`unexpected fetch: ${url}`);
    }),
  );
}

function statusOf(familyId: string): string | null {
  return screen
    .getByTestId('students-rows')
    .querySelector(`[data-family="${familyId}"]`)
    ?.getAttribute('data-status') ?? null;
}

describe('StudentsTab', () => {
  beforeEach(() => mockApi());
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('search matches a FAMILY name', async () => {
    render(<StudentsTab onSelectFamily={() => {}} />);
    await screen.findByTestId('students-search');

    fireEvent.change(screen.getByTestId('students-search'), {
      target: { value: 'Working' },
    });

    await waitFor(() => expect(screen.getByTestId('students-rows')).toBeInTheDocument());
    const rows = screen.getAllByTestId('student-row');
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveAttribute('data-family', 'fam-work');
  });

  it('search matches a STUDENT name (D-17)', async () => {
    render(<StudentsTab onSelectFamily={() => {}} />);
    await screen.findByTestId('students-search');

    // "Mateo" is fam-work's child; no family display_name contains it.
    fireEvent.change(screen.getByTestId('students-search'), {
      target: { value: 'Mateo' },
    });

    await waitFor(() => expect(screen.getByTestId('students-rows')).toBeInTheDocument());
    const rows = screen.getAllByTestId('student-row');
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveAttribute('data-family', 'fam-work');
  });

  it('renders each of the four status chips for its state (D-18 precedence)', async () => {
    render(<StudentsTab onSelectFamily={() => {}} />);
    await screen.findByTestId('students-search');

    // "Family" appears in every display_name → all four rows render at once.
    fireEvent.change(screen.getByTestId('students-search'), {
      target: { value: 'Family' },
    });

    await waitFor(() =>
      expect(screen.getAllByTestId('student-row')).toHaveLength(4),
    );

    // Awaiting SIS outranks the working recovery_state it also has (precedence).
    expect(statusOf('fam-await')).toBe('awaiting_sis');
    expect(statusOf('fam-closed')).toBe('closed');
    expect(statusOf('fam-work')).toBe('working');
    expect(statusOf('fam-none')).toBe('no_contact');

    const board = screen.getByTestId('students-rows');
    expect(board).toHaveTextContent('Awaiting SIS');
    expect(board).toHaveTextContent('Closed');
    expect(board).toHaveTextContent('Working');
    expect(board).toHaveTextContent('No Contact');
  });

  it('a row click fires onSelectFamily with the family id', async () => {
    const onSelectFamily = vi.fn();
    render(<StudentsTab onSelectFamily={onSelectFamily} />);
    await screen.findByTestId('students-search');

    fireEvent.change(screen.getByTestId('students-search'), {
      target: { value: 'Closed' },
    });
    const row = await screen.findByTestId('student-row');
    fireEvent.click(row);

    expect(onSelectFamily).toHaveBeenCalledWith('fam-closed');
  });

  it('shows an empty state before any query is typed', async () => {
    render(<StudentsTab onSelectFamily={() => {}} />);
    await screen.findByTestId('students-search');
    expect(screen.getByTestId('empty-state')).toBeInTheDocument();
  });
});
