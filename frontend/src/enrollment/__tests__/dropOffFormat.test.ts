import { describe, expect, it } from 'vitest';
import { dropOffPath, humanizeSegment } from '../format';

// Humanization for the apply-flow drop-off telemetry (S15 W2). snake_case →
// Title Case; null/empty segments dropped so the operator never sees "Null".

describe('humanizeSegment', () => {
  it('title-cases a snake_case form key', () => {
    expect(humanizeSegment('data_collection_consent')).toBe(
      'Data Collection Consent',
    );
  });

  it('title-cases a single word', () => {
    expect(humanizeSegment('signature')).toBe('Signature');
    expect(humanizeSegment('enroll')).toBe('Enroll');
  });

  it('returns empty string for null/undefined/empty', () => {
    expect(humanizeSegment(null)).toBe('');
    expect(humanizeSegment(undefined)).toBe('');
    expect(humanizeSegment('')).toBe('');
  });
});

describe('dropOffPath', () => {
  it('joins step · form · field, humanized', () => {
    expect(
      dropOffPath('enroll', 'data_collection_consent', 'signature'),
    ).toBe('Enroll · Data Collection Consent · Signature');
  });

  it('omits null form/field segments', () => {
    expect(dropOffPath('apply', null, null)).toBe('Apply');
    expect(dropOffPath('enroll', 'health_form', null)).toBe(
      'Enroll · Health Form',
    );
  });
});
