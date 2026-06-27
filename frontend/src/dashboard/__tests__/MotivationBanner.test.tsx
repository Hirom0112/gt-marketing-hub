import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import MotivationBanner from '../MotivationBanner';

// Acceptance test (CLAUDE §4.2) for the daily Motivation banner (R6 / D-11).
// Local-only chrome: ONE quote shows, rotating daily by day-of-year; the agent
// may edit it; a custom edit persists to localStorage['gtpulse.motd.<agentId>']
// and is shown in place of the rotation on later mounts. No fetch, no backend.

const AGENT = 'agent-7';
const KEY = `gtpulse.motd.${AGENT}`;

describe('MotivationBanner (R6 / D-11)', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });
  afterEach(() => {
    window.localStorage.clear();
  });

  it('shows one motivational quote (the daily rotation, no stored custom)', () => {
    render(<MotivationBanner agentId={AGENT} />);
    const quote = screen.getByTestId('motivation-quote');
    expect(quote).toBeInTheDocument();
    expect(quote.textContent?.trim().length ?? 0).toBeGreaterThan(0);
    // Exactly one quote — no carousel/feed.
    expect(screen.getAllByTestId('motivation-quote')).toHaveLength(1);
  });

  it('edits + saves the quote, persisting to localStorage and re-rendering', () => {
    render(<MotivationBanner agentId={AGENT} />);

    fireEvent.click(screen.getByTestId('motivation-edit'));
    const input = screen.getByTestId('motivation-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'Keep going, you got this.' } });
    fireEvent.click(screen.getByTestId('motivation-save'));

    // Persisted under the agent-scoped key.
    expect(window.localStorage.getItem(KEY)).toBe('Keep going, you got this.');
    // Re-rendered the custom quote (back to the read view).
    expect(screen.getByTestId('motivation-quote')).toHaveTextContent(
      'Keep going, you got this.',
    );
    expect(screen.queryByTestId('motivation-input')).not.toBeInTheDocument();
  });

  it('shows the stored custom quote on a fresh mount', () => {
    window.localStorage.setItem(KEY, 'Stored from a prior day.');
    render(<MotivationBanner agentId={AGENT} />);
    expect(screen.getByTestId('motivation-quote')).toHaveTextContent(
      'Stored from a prior day.',
    );
  });

  it('persists per agent · a different agent does not see the stored quote', () => {
    window.localStorage.setItem(KEY, 'Agent seven only.');
    render(<MotivationBanner agentId="agent-9" />);
    expect(screen.getByTestId('motivation-quote')).not.toHaveTextContent(
      'Agent seven only.',
    );
  });

  it('a blank save is ignored (keeps the current quote)', () => {
    render(<MotivationBanner agentId={AGENT} />);
    const before = screen.getByTestId('motivation-quote').textContent;

    fireEvent.click(screen.getByTestId('motivation-edit'));
    const input = screen.getByTestId('motivation-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '   ' } });
    fireEvent.click(screen.getByTestId('motivation-save'));

    expect(window.localStorage.getItem(KEY)).toBeNull();
    expect(screen.getByTestId('motivation-quote').textContent).toBe(before);
  });
});
