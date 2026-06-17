// R3 — strict TDD (red→green) for the pure `deriveNextStep` deriver.
//
// `deriveNextStep(stage, funding_state, forms, params)` → `{label, byWhen}` is the
// family-facing "what do I do next + by when" deriver. It re-homes the dead
// backend `derive_stall_reason` rule table (stage_machine.py:94) as a pure,
// params-driven, NO-LLM client function. Deadlines come from params (INV-11), not
// invented numbers. The voucher lane is FAIL-CLOSED: a family is never told
// "confirmed" before the first installment is received (INV-10).

import { describe, expect, it } from 'vitest';
import { deriveNextStep, type ApplyStage } from '../lib/deriveNextStep';
import { APPLY_PARAMS } from '../lib/params';

// A fixed reference "now" so byWhen is deterministic under test.
const NOW = new Date('2026-06-17T00:00:00.000Z');

function daysFromNow(n: number): string {
  return new Date(NOW.getTime() + n * 86_400_000).toISOString().slice(0, 10);
}

const noForms = { signed: 0, total: 6 };
const partialForms = { signed: 3, total: 6 };
const allForms = { signed: 6, total: 6 };

describe('deriveNextStep (R3 — pure, params-driven, no LLM)', () => {
  it('interest → "submit your application", due within the stall window', () => {
    const r = deriveNextStep('interest', 'none', noForms, APPLY_PARAMS, NOW);
    expect(r.label.toLowerCase()).toContain('application');
    expect(r.byWhen).toBe(daysFromNow(APPLY_PARAMS.stallWindowDays));
  });

  it('apply (submitted, no forms) → "start your enrollment forms"', () => {
    const r = deriveNextStep('apply', 'none', noForms, APPLY_PARAMS, NOW);
    expect(r.label.toLowerCase()).toContain('enrollment');
    expect(r.byWhen).toBe(daysFromNow(APPLY_PARAMS.stallWindowDays));
  });

  it('enroll (forms partial) → "finish your enrollment forms" with the count', () => {
    const r = deriveNextStep('enroll', 'none', partialForms, APPLY_PARAMS, NOW);
    expect(r.label.toLowerCase()).toContain('enrollment');
    // surfaces the X of Y progress
    expect(r.label).toContain('3');
    expect(r.label).toContain('6');
    expect(r.byWhen).toBe(daysFromNow(APPLY_PARAMS.stallWindowDays));
  });

  it('tuition + voucher awarded-but-not-confirmed → "reconfirm voucher", due within the deadline horizon (fail-closed)', () => {
    const r = deriveNextStep('tuition', 'awarded_selfreport', allForms, APPLY_PARAMS, NOW);
    expect(r.label.toLowerCase()).toContain('reconfirm');
    // the reconfirm gap is keyed to the deadline horizon, not the stall window
    expect(r.byWhen).toBe(daysFromNow(APPLY_PARAMS.deadlineHorizonDays));
  });

  it('tuition + gt_confirmed but no installment yet → still "reconfirm/confirm", NEVER "all set" (fail-closed, INV-10)', () => {
    const r = deriveNextStep('tuition', 'gt_confirmed', allForms, APPLY_PARAMS, NOW);
    expect(r.label.toLowerCase()).not.toContain('all set');
    expect(r.byWhen).toBe(daysFromNow(APPLY_PARAMS.deadlineHorizonDays));
  });

  it('tuition + first_installment_received → "you\'re all set", no deadline', () => {
    const r = deriveNextStep('tuition', 'first_installment_received', allForms, APPLY_PARAMS, NOW);
    expect(r.label.toLowerCase()).toContain('all set');
    expect(r.byWhen).toBeNull();
  });

  it('tuition + funded → "you\'re all set", no deadline', () => {
    const r = deriveNextStep('tuition', 'funded', allForms, APPLY_PARAMS, NOW);
    expect(r.label.toLowerCase()).toContain('all set');
    expect(r.byWhen).toBeNull();
  });

  it('is pure/deterministic for the same inputs', () => {
    const a = deriveNextStep('enroll', 'applied', partialForms, APPLY_PARAMS, NOW);
    const b = deriveNextStep('enroll', 'applied', partialForms, APPLY_PARAMS, NOW);
    expect(a).toEqual(b);
  });

  it('reads the deadline horizon from params (drifts when params drift, INV-11)', () => {
    const tweaked = { ...APPLY_PARAMS, deadlineHorizonDays: 30 };
    const r = deriveNextStep('tuition', 'awarded_selfreport', allForms, tweaked, NOW);
    expect(r.byWhen).toBe(daysFromNow(30));
  });

  // Type-level: ApplyStage is the closed 4-stage set.
  it('accepts every pipeline stage', () => {
    const stages: ApplyStage[] = ['interest', 'apply', 'enroll', 'tuition'];
    for (const s of stages) {
      expect(deriveNextStep(s, 'none', noForms, APPLY_PARAMS, NOW).label.length).toBeGreaterThan(0);
    }
  });
});
