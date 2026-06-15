// Shared semantic tone helpers for the UI primitives. The four tones map onto
// the theme.css palette: neutral (editorial base) + the three accents.
// Centralised here so every primitive resolves a tone identically — no
// per-component wash/ink guessing.

export type Tone = 'neutral' | 'signal' | 'flow' | 'gate';

export interface ToneVars {
  fg: string; // AA-safe foreground on the wash
  wash: string; // tinted background
  solid: string; // saturated accent (borders / solid fills)
}

export function toneVars(tone: Tone): ToneVars {
  switch (tone) {
    case 'signal':
      return {
        fg: 'var(--signal-ink)',
        wash: 'var(--signal-wash)',
        solid: 'var(--signal)',
      };
    case 'flow':
      return {
        fg: 'var(--flow-ink)',
        wash: 'var(--flow-wash)',
        solid: 'var(--flow)',
      };
    case 'gate':
      return {
        fg: 'var(--gate-ink)',
        wash: 'var(--gate-wash)',
        solid: 'var(--gate)',
      };
    case 'neutral':
    default:
      return {
        fg: 'var(--muted)',
        wash: 'var(--paper)',
        solid: 'var(--line-strong)',
      };
  }
}
