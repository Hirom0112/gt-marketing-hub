import { render, screen, fireEvent, within, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Acceptance test (CLAUDE §4.2) for the composable Home (TODO_v2 §B3). It asserts
// the PERSISTENCE CONTRACT against the backend `/home/layout` seam, not RGL's DOM:
// react-grid-layout is mocked to a plain passthrough that still surfaces
// onLayoutChange, so the test isolates the Home's fetch + debounced PUT wiring.

// --- apiFetch mock (the /home/layout seam) ---------------------------------
// Per-test the GET body is overridden via `getLayout`; PUT calls are recorded.
let getLayout: unknown = [];
let getOk = true;
const putBodies: unknown[] = [];

const apiFetch = vi.fn((path: string, init?: RequestInit) => {
  if (init?.method === 'PUT') {
    putBodies.push(JSON.parse(String(init.body)));
    return Promise.resolve({ ok: true, json: async () => getLayout } as Response);
  }
  // GET /home/layout (and any incidental widget reads from real components fall
  // through to a benign empty/ok response).
  if (path === '/home/layout') {
    return Promise.resolve({
      ok: getOk,
      json: async () => getLayout,
    } as Response);
  }
  return Promise.resolve({ ok: true, json: async () => [] } as Response);
});

vi.mock('../../config', () => ({
  apiFetch: (path: string, init?: RequestInit) => apiFetch(path, init),
}));

// --- react-grid-layout mock ------------------------------------------------
// A plain div that renders its children and exposes a button to fire
// onLayoutChange (proving the debounced-persist path). useContainerWidth is
// stubbed so the component renders in jsdom.
vi.mock('react-grid-layout', () => {
  const GridLayout = ({
    children,
    onLayoutChange,
    layout,
  }: {
    children: React.ReactNode;
    onLayoutChange?: (l: Array<{ i: string; x: number; y: number; w: number; h: number }>) => void;
    layout: Array<{ i: string; x: number; y: number; w: number; h: number }>;
  }): JSX.Element => (
    <div data-testid="rgl">
      <button
        data-testid="rgl-fire-change"
        onClick={() =>
          onLayoutChange?.(layout.map((p) => ({ ...p, x: p.x + 1 })))
        }
      >
        fire
      </button>
      {children}
    </div>
  );
  return {
    __esModule: true,
    default: GridLayout,
    GridLayout,
    useContainerWidth: () => ({
      width: 1200,
      mounted: true,
      containerRef: { current: null },
      measureWidth: () => {},
    }),
  };
});

// Stub the heavy real cockpit surfaces the registry maps the starter ids to, so a
// rendered tile's BODY is trivial — the registry grouping/labels (what this test
// asserts) come from the real widgetRegistry, unaffected by these stubs.
vi.mock('../../PipelineBoard', () => ({ default: () => <div data-testid="x-pipeline" /> }));
vi.mock('../../Scoreboard', () => ({ default: () => <div data-testid="x-scoreboard" /> }));
vi.mock('../../security/SecurityTab', () => ({ default: () => <div data-testid="x-security" /> }));
vi.mock('../../workspaces/DecisionQueueWorkspace', () => ({
  default: () => <div data-testid="x-decisions" />,
}));
vi.mock('../../dashboard/DataConfidenceBanner', () => ({
  default: () => <div data-testid="x-dataconf" />,
}));

import ComposableHome from '../ComposableHome';

// Two PLACEHOLDER ids (light to render) — avoids mounting heavy real surfaces that
// self-fetch (e.g. geo_module → GeoBoard, content_library → ContentWorkspace). This
// suite tests the persistence seam, not widget rendering, so it uses inert tiles.
const SCHED = { i: 'scheduler', x: 0, y: 0, w: 4, h: 4 };
const RECIPES = { i: 'recipes', x: 4, y: 0, w: 4, h: 4 };

describe('ComposableHome', () => {
  beforeEach(() => {
    apiFetch.mockClear();
    putBodies.length = 0;
    getLayout = [SCHED, RECIPES];
    getOk = true;
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });

  async function flushGet(): Promise<void> {
    // Resolve the GET promise chain (microtasks) under fake timers.
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  }

  it('renders the widgets returned by GET /home/layout', async () => {
    render(<ComposableHome />);
    await flushGet();
    expect(apiFetch.mock.calls.some((c) => c[0] === '/home/layout')).toBe(true);
    expect(screen.getAllByText('Scheduler').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Recipes').length).toBeGreaterThan(0);
    // The tile headers carry the remove affordance — proof the placements rendered.
    expect(screen.getByRole('button', { name: 'Remove Scheduler' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Remove Recipes' })).toBeInTheDocument();
  });

  it('lists the grouped widgets in the picker and checking one adds it + debounced PUT', async () => {
    render(<ComposableHome />);
    await flushGet();

    // Open the grouped picker.
    fireEvent.click(screen.getByRole('button', { name: 'Add widgets' }));
    const picker = screen.getByTestId('widget-picker');
    // Sectioned by group.
    expect(within(picker).getByText('Enrollment')).toBeInTheDocument();
    expect(within(picker).getByText('Marketing')).toBeInTheDocument();
    expect(within(picker).getByText('Security')).toBeInTheDocument();

    // A not-yet-present widget is unchecked; check it to add.
    const box = within(picker).getByRole('checkbox', { name: 'Sentiment' });
    expect(box).not.toBeChecked();
    fireEvent.click(box);

    // The widget tile appears immediately…
    expect(screen.getAllByText('Sentiment').length).toBeGreaterThan(0);

    // …and the PUT is debounced (nothing yet before the timer fires).
    expect(putBodies).toHaveLength(0);
    act(() => {
      vi.advanceTimersByTime(500);
    });
    expect(putBodies).toHaveLength(1);
    const body = putBodies[0] as { layout: Array<{ i: string }> };
    expect(body.layout.map((p) => p.i)).toContain('sentiment');
  });

  it('unchecking a present widget removes it (and persists)', async () => {
    render(<ComposableHome />);
    await flushGet();
    fireEvent.click(screen.getByRole('button', { name: 'Add widgets' }));
    const picker = screen.getByTestId('widget-picker');

    const box = within(picker).getByRole('checkbox', { name: 'Scheduler' });
    expect(box).toBeChecked();
    fireEvent.click(box);

    // The tile is gone (only the picker label may remain, never a tile header).
    expect(
      screen.queryByRole('button', { name: 'Remove Scheduler' }),
    ).toBeNull();

    act(() => {
      vi.advanceTimersByTime(500);
    });
    const body = putBodies[putBodies.length - 1] as { layout: Array<{ i: string }> };
    expect(body.layout.map((p) => p.i)).not.toContain('scheduler');
  });

  it('persists a debounced PUT when RGL fires onLayoutChange', async () => {
    render(<ComposableHome />);
    await flushGet();
    fireEvent.click(screen.getByTestId('rgl-fire-change'));
    expect(putBodies).toHaveLength(0); // debounced
    act(() => {
      vi.advanceTimersByTime(500);
    });
    expect(putBodies).toHaveLength(1);
  });

  it('renders without crashing when GET /home/layout fails (fail-safe starter pack)', async () => {
    getOk = false;
    render(<ComposableHome />);
    await flushGet();
    // Fail-safe: the starter pack renders rather than crashing/empty.
    expect(screen.getByTestId('composable-home')).toBeInTheDocument();
    expect(screen.getByText('Pipeline Board')).toBeInTheDocument();
  });
});
