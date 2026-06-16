// Unit tests for the apply data layer — the INV-1/INV-6 guardrail at the source.

import { describe, expect, it } from 'vitest';
import {
  createFamily,
  emitEvent,
  ensureAnonSession,
  submitApply,
  submitEnroll,
  submitInterest,
  submitTuition,
  type ApplySession,
  type ApplyEvent,
} from '../lib/apply';
import {
  generateSyntheticIdentity,
  SYNTHETIC_EMAIL_DOMAIN,
} from '../lib/identity';
import { makeMockSupabase } from './mockSupabase';

// The set of keys an apply_events row may ever carry. NO "value", no "content",
// no child key. If a future change adds one, this test fails.
const ALLOWED_EVENT_KEYS = new Set([
  'event_id',
  'family_id',
  'step',
  'field_key',
  'event_type',
  'time_on_step_ms',
]);

// Keys that would indicate a leaked selected value or child identity.
const FORBIDDEN_SUBSTRINGS = ['value', 'content', 'child', 'student', 'dob'];

describe('synthetic identity (INV-1)', () => {
  it('always generates an @example.invalid email', () => {
    for (let i = 0; i < 200; i++) {
      const id = generateSyntheticIdentity();
      expect(id.email.endsWith(SYNTHETIC_EMAIL_DOMAIN)).toBe(true);
    }
  });

  it('never includes a child/student/DOB field', () => {
    const id = generateSyntheticIdentity() as unknown as Record<
      string,
      unknown
    >;
    for (const key of Object.keys(id)) {
      expect(['child', 'student', 'dob']).not.toContain(key.toLowerCase());
    }
  });
});

async function walk(): Promise<ReturnType<typeof makeMockSupabase>> {
  const sb = makeMockSupabase();
  const uid = await ensureAnonSession(sb);
  const session = await createFamily(sb, uid, 'referral');
  const answers = {
    product_interest: 'anywhere',
    attribution_source: 'referral',
    region: 'Southwest',
    grade_interest: '3',
    num_children: 2,
  } as const;
  await submitInterest(sb, session, answers);
  await submitApply(sb, session);
  await submitEnroll(sb, session, 6);
  await submitTuition(sb, session, 'self_pay');
  await emitEvent(sb, {
    family_id: session.familyId,
    step: 'interest',
    field_key: 'num_children',
    event_type: 'step_completed',
    time_on_step_ms: 4200,
  });
  return sb;
}

describe('intake write order + RLS-compatible shapes', () => {
  it('writes family_record first, then leads_new, in dependency order', async () => {
    const sb = await walk();
    const tables = sb.inserts.map((i) => i.table);
    expect(tables[0]).toBe('family_record');
    expect(tables.indexOf('family_record')).toBeLessThan(
      tables.indexOf('leads_new'),
    );
  });

  it('every persisted email ends @example.invalid', async () => {
    const sb = await walk();
    const fr = sb.rowsFor('family_record')[0]!;
    const lead = sb.rowsFor('leads_new')[0]!;
    expect(
      String(fr.primary_contact_synthetic_email).endsWith(
        SYNTHETIC_EMAIL_DOMAIN,
      ),
    ).toBe(true);
    expect(
      String(lead.synthetic_email).endsWith(SYNTHETIC_EMAIL_DOMAIN),
    ).toBe(true);
  });

  it('family_record.user_id is the authed uid (RLS owner-scope)', async () => {
    const sb = await walk();
    const fr = sb.rowsFor('family_record')[0]!;
    expect(fr.user_id).toBe(sb.uid);
  });

  it('leads_new carries num_children (the value term, A-23)', async () => {
    const sb = await walk();
    const lead = sb.rowsFor('leads_new')[0]!;
    expect(lead.num_children).toBe(2);
  });
});

describe('apply_events are metadata-only (INV-1/INV-6/COPPA)', () => {
  it('only emits allowed keys and never a value/content/child key', async () => {
    const sb = await walk();
    const events = sb.rowsFor('apply_events');
    expect(events.length).toBeGreaterThan(0);
    for (const ev of events) {
      for (const key of Object.keys(ev)) {
        expect(ALLOWED_EVENT_KEYS.has(key)).toBe(true);
        for (const bad of FORBIDDEN_SUBSTRINGS) {
          expect(key.toLowerCase()).not.toContain(bad);
        }
      }
    }
  });

  it('field_key is a field NAME, never the chosen value', async () => {
    const sb = await walk();
    const ev = sb
      .rowsFor('apply_events')
      .find((e) => e.field_key !== null)!;
    // The recorded field_key matches a known field name, not an option token.
    expect(ev.field_key).toBe('num_children');
    // It must NOT be one of the value tokens the user actually selected.
    expect(['anywhere', 'referral', 'Southwest', '3', 2]).not.toContain(
      ev.field_key,
    );
  });

  it('the ApplyEvent type cannot carry a value (compile-time + runtime)', async () => {
    // Constructing a well-typed event yields exactly the metadata fields.
    const ev: ApplyEvent = {
      family_id: 'f',
      step: 'interest',
      field_key: 'region',
      event_type: 'field_focused',
      time_on_step_ms: 10,
    };
    expect(Object.keys(ev).sort()).toEqual(
      ['event_type', 'family_id', 'field_key', 'step', 'time_on_step_ms'].sort(),
    );
  });
});

describe('failure handling', () => {
  it('surfaces a leads_new insert failure to the caller', async () => {
    const sb = makeMockSupabase({ failInsertOn: 'leads_new' });
    const uid = await ensureAnonSession(sb);
    const session: ApplySession = await createFamily(sb, uid, 'direct');
    await expect(
      submitInterest(sb, session, {
        product_interest: 'campus',
        attribution_source: 'direct',
        region: 'Midwest',
        grade_interest: 'K',
        num_children: 1,
      }),
    ).rejects.toThrow(/leads_new/);
  });

  it('emitEvent never throws even if the insert fails (best-effort telemetry)', async () => {
    const sb = makeMockSupabase({ failInsertOn: 'apply_events' });
    await expect(
      emitEvent(sb, {
        family_id: 'f',
        step: 'interest',
        field_key: null,
        event_type: 'step_viewed',
        time_on_step_ms: 0,
      }),
    ).resolves.toBeUndefined();
  });
});
