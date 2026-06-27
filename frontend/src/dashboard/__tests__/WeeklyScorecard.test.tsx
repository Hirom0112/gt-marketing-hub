import { render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import WeeklyScorecard from '../WeeklyScorecard';

// Acceptance test (CLAUDE §4.2) for the weekly KPI scorecard (TODO_v2 §B5). The
// table reads `GET /scorecard/weekly` — one row per metric with this/last week, a
// signed delta, a 4-week sparkline, target, a green/yellow/red status pill, and a
// pace projection. apiFetch is mocked; the component is otherwise unchanged.

const SCORECARD = {
  as_of: '2026-06-22',
  metrics: [
    {
      key: 'proposals',
      label: 'AI proposals',
      this_week: 7,
      last_week: 4,
      delta: 3, // positive — improving
      sparkline: [2, 4, 4, 7],
      target: 5,
      status: 'green',
      projection: 9,
    },
    {
      key: 'approvals',
      label: 'Human approvals',
      this_week: 1,
      last_week: 4,
      delta: -3, // negative — declining
      sparkline: [4, 3, 2, 1],
      target: 4,
      status: 'red',
      projection: 0,
    },
  ],
};

function mockApi(impl: () => Promise<unknown>): ReturnType<typeof vi.fn> {
  // Extra (path, init) call args are ignored — every case resolves the same impl.
  return vi.fn(impl);
}

let apiFetchMock = mockApi(() =>
  Promise.resolve({ ok: true, json: async () => SCORECARD } as Response),
);

vi.mock('../../config', () => ({
  apiFetch: (path: string, init?: RequestInit) => apiFetchMock(path, init),
}));

afterEach(() => {
  vi.clearAllMocks();
  apiFetchMock = mockApi(() =>
    Promise.resolve({ ok: true, json: async () => SCORECARD } as Response),
  );
});

describe('WeeklyScorecard', () => {
  it('renders one row per metric with label / this_week / last_week', async () => {
    render(<WeeklyScorecard />);
    const rows = await screen.findAllByTestId('scorecard-row');
    expect(rows).toHaveLength(2);

    const proposals = rows[0]!;
    expect(within(proposals).getByText('AI proposals')).toBeInTheDocument();
    // this_week (7) and last_week (4) both render in the row.
    expect(within(proposals).getByText('7')).toBeInTheDocument();
    expect(within(proposals).getByText('4')).toBeInTheDocument();
  });

  it('shows the signed delta (positive vs negative)', async () => {
    render(<WeeklyScorecard />);
    const deltas = await screen.findAllByTestId('delta');
    // First metric rose (+3), second fell (−3).
    expect(deltas[0]!).toHaveTextContent('+3');
    expect(deltas[1]!).toHaveTextContent('-3');
  });

  it('reflects the status in the pill — a red metric renders the red (signal) treatment', async () => {
    render(<WeeklyScorecard />);
    const rows = await screen.findAllByTestId('scorecard-row');
    // green metric → flow-wash pill; red metric → signal-wash pill.
    const greenPill = within(rows[0]!).getByText('GREEN');
    const redPill = within(rows[1]!).getByText('RED');
    expect(greenPill).toBeInTheDocument();
    expect(redPill).toBeInTheDocument();
    // The red treatment uses the app's signal tone wash (no invented palette).
    expect(redPill).toHaveStyle({ background: 'var(--signal-wash)' });
    expect(greenPill).toHaveStyle({ background: 'var(--flow-wash)' });
  });

  it('renders a sparkline (an inline svg polyline) per row', async () => {
    render(<WeeklyScorecard />);
    const sparks = await screen.findAllByTestId('sparkline');
    expect(sparks).toHaveLength(2);
    const line = within(sparks[0]!).getByTestId('sparkline-line');
    // The polyline carries point coords for the 4 trailing weekly values.
    expect(line.getAttribute('points')).toBeTruthy();
    expect(line.tagName.toLowerCase()).toBe('polyline');
  });

  it('shows the as_of caption and the pace projection', async () => {
    render(<WeeklyScorecard />);
    expect(await screen.findByTestId('scorecard-asof')).toHaveTextContent(
      'week of 2026-06-22',
    );
    const projections = screen.getAllByTestId('projection');
    expect(projections[0]!).toHaveTextContent('at this pace → 9');
  });

  it('fails safe on a fetch error — a quiet notice, no crash', async () => {
    apiFetchMock = mockApi(() => Promise.reject(new Error('network down')));
    render(<WeeklyScorecard />);
    await waitFor(() =>
      expect(screen.getByText('Scorecard unavailable')).toBeInTheDocument(),
    );
    expect(screen.queryAllByTestId('scorecard-row')).toHaveLength(0);
  });
});
