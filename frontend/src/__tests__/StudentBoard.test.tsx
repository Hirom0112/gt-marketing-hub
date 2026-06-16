import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import StudentBoard from '../enrollment/StudentBoard';

// Acceptance test (CLAUDE §4.2). The per-child board (A-24) fetches GET /students
// — households the server has already ranked (households by their top child,
// students within a household by recoverable_now desc) — and renders them IN THE
// ORDER RECEIVED (no client-side re-sort). Every ROW is a STUDENT (one
// application per child) with a distinct label, grouped under its household.

const BOARD_PAYLOAD = {
  total_students: 3,
  total_value_at_risk: 31422,
  households: [
    {
      family_id: 'fam-rivera',
      household_name: 'The Rivera Family',
      value_at_risk: 20948,
      students: [
        {
          student_id: 'stu-alex',
          family_id: 'fam-rivera',
          household_name: 'The Rivera Family',
          display_label: 'Rivera household — Alex · Grade 3',
          synthetic_first_name: 'Alex',
          grade: '3',
          current_stage: 'enroll',
          funding_type: 'tefa_standard',
          funding_state: 'awarded_selfreport',
          stall_reason: 'forms_partial',
          score: 0.82,
          recoverability: 0.9,
          value: 10474,
          recoverable_now: 8200,
          freshness: 0.8,
          recovery_state: 'stalled',
        },
        {
          student_id: 'stu-bea',
          family_id: 'fam-rivera',
          household_name: 'The Rivera Family',
          display_label: 'Rivera household — Bea · Grade 1',
          synthetic_first_name: 'Bea',
          grade: '1',
          current_stage: 'apply',
          funding_type: 'tefa_standard',
          funding_state: 'applied',
          stall_reason: null,
          score: 0.55,
          recoverability: 0.5,
          value: 10474,
          recoverable_now: 4100,
          freshness: 0.7,
          recovery_state: 'stalled',
        },
      ],
    },
    {
      family_id: 'fam-chen',
      household_name: 'The Chen Family',
      value_at_risk: 10474,
      students: [
        {
          student_id: 'stu-cody',
          family_id: 'fam-chen',
          household_name: 'The Chen Family',
          display_label: 'Chen household — Cody · Grade 5',
          synthetic_first_name: 'Cody',
          grade: '5',
          current_stage: 'interest',
          funding_type: 'self_pay',
          funding_state: 'none',
          stall_reason: 'no_response',
          score: 0.3,
          recoverability: 0.4,
          value: 10474,
          recoverable_now: 1500,
          freshness: 0.5,
          recovery_state: 'stalled',
        },
      ],
    },
  ],
};

function stubFetchOk(): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => BOARD_PAYLOAD,
    })),
  );
}

describe('StudentBoard', () => {
  beforeEach(stubFetchOk);
  afterEach(() => vi.unstubAllGlobals());

  it('renders one row per child, grouped by household, in received order', async () => {
    render(<StudentBoard />);
    await screen.findByTestId('student-board');

    // Two household groups; three student rows total (one application per child).
    const groups = screen.getAllByTestId('household-group');
    expect(groups).toHaveLength(2);
    expect(screen.getAllByTestId('student-row')).toHaveLength(3);

    // Distinct per-student labels (also de-dupes the same-surname board problem).
    expect(
      screen.getByText('Rivera household — Alex · Grade 3'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Rivera household — Bea · Grade 1'),
    ).toBeInTheDocument();
    expect(
      screen.getByText('Chen household — Cody · Grade 5'),
    ).toBeInTheDocument();

    // Rivera's two children render in the received order (Alex before Bea).
    const rivera = groups[0] as HTMLElement;
    const labels = within(rivera)
      .getAllByTestId('student-row')
      .map((row) => row.textContent ?? '');
    expect(labels[0]).toContain('Alex');
    expect(labels[1]).toContain('Bea');
  });

  it('shows the household $-at-risk and the board totals', async () => {
    render(<StudentBoard />);
    await screen.findByTestId('student-board');

    // The roll-up the situation reads: total students + total value at risk.
    expect(screen.getByTestId('student-board-total')).toHaveTextContent(
      '3 students',
    );

    // Each household header shows its own $-at-risk and child count.
    const atRisk = screen.getAllByTestId('household-value-at-risk');
    expect(atRisk[0]).toHaveTextContent('2 children');
    expect(atRisk[1]).toHaveTextContent('1 child');
  });

  it('selecting a student row reports its household family_id', async () => {
    const onSelectFamily = vi.fn();
    render(<StudentBoard onSelectFamily={onSelectFamily} />);
    await screen.findByTestId('student-board');

    fireEvent.click(screen.getByTestId('student-row-stu-alex'));
    expect(onSelectFamily).toHaveBeenCalledWith('fam-rivera');
  });

  it('surfaces a fetch error without crashing', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false, status: 500, json: async () => ({}) })),
    );
    render(<StudentBoard />);
    await waitFor(() =>
      expect(screen.getByTestId('student-board-error')).toBeInTheDocument(),
    );
  });

  it('defaults to the active scope and refetches when a scope tab is clicked', async () => {
    render(<StudentBoard />);
    await screen.findByTestId('student-board');

    // The board opens on the active slice (closed-out children don't lead it).
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('scope=active');

    // Switching to History refetches the server's history slice (server owns the
    // filter; this UI never re-derives recovery state).
    fireEvent.click(screen.getByTestId('student-scope-history'));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some((call) =>
          String(call[0]).includes('scope=history'),
        ),
      ).toBe(true),
    );
  });
});
