import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { WIDGET_BY_ID } from '../widgetRegistry';

// TODO_v2 §B5: the `kpi_scorecard` B3 widget id is now wired to the REAL weekly
// scorecard (not a WidgetPlaceholder). This guards both the registry mapping and
// that the tile renders the live scorecard surface.

const SCORECARD = {
  as_of: '2026-06-22',
  metrics: [
    {
      key: 'proposals',
      label: 'AI proposals',
      this_week: 7,
      last_week: 4,
      delta: 3,
      sparkline: [2, 4, 4, 7],
      target: 5,
      status: 'green',
      projection: 9,
    },
  ],
};

vi.mock('../../config', () => ({
  apiFetch: vi.fn(() =>
    Promise.resolve({ ok: true, json: async () => SCORECARD } as Response),
  ),
}));

afterEach(() => {
  vi.clearAllMocks();
});

describe('kpi_scorecard Home widget', () => {
  it('is registered under the exact id and is NOT a placeholder', () => {
    const def = WIDGET_BY_ID.get('kpi_scorecard');
    expect(def).toBeDefined();
    // The placeholder helper tags its components `Placeholder(...)`; the real one
    // does not. (Honest guard: a regression back to a placeholder is caught here.)
    expect(def!.Component.displayName ?? def!.Component.name).not.toMatch(
      /^Placeholder/,
    );
  });

  it('renders the real weekly scorecard surface (not the placeholder tile)', async () => {
    const def = WIDGET_BY_ID.get('kpi_scorecard');
    const Widget = def!.Component;
    render(<Widget />);
    // The scorecard marker appears; the placeholder "SURFACE COMING SOON" does not.
    expect(await screen.findByTestId('weekly-scorecard')).toBeInTheDocument();
    expect(await screen.findByText('AI proposals')).toBeInTheDocument();
    expect(screen.queryByText('SURFACE COMING SOON')).not.toBeInTheDocument();
  });
});
