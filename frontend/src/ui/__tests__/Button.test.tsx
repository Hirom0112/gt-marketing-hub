import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Button } from '../Button';

// Acceptance test (CLAUDE §4.2). The cockpit adds two Button variants: `flow`
// (teal primary — the affirmative Capture/send action) and `on-ink` (a ghost
// button that lives on a dark dock: transparent fill, white text, faint white
// border). Both resolve through tokens — no raw hex except the on-ink hairline.

describe('Button · cockpit variants', () => {
  it('flow renders the teal primary fill from tokens', () => {
    render(<Button variant="flow">Capture all</Button>);
    const btn = screen.getByRole('button', { name: 'Capture all' });
    expect(btn.style.background).toBe('var(--flow)');
    expect(btn.style.color).toBe('var(--on-ink)');
    expect(btn.style.border).toContain('var(--flow)');
  });

  it('on-ink is a transparent ghost with white text and a faint border', () => {
    render(<Button variant="on-ink">Send nudge</Button>);
    const btn = screen.getByRole('button', { name: 'Send nudge' });
    expect(btn.style.background).toBe('transparent');
    expect(btn.style.color).toBe('var(--on-ink)');
    expect(btn.style.border).toContain('rgba(255, 255, 255, 0.25)');
  });
});
