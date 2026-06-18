// Unit tests for the PRODUCTION demo session-swap (DH-0b Part A): the
// `asDemoSupabase` builder + the `loadDemoSessions` env loader. These prove the
// live switcher's restore path against the existing DemoSupabase contract —
// anon-only, no service_role (INV-5), synthetic-only (INV-1).

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  asDemoSupabase,
  isDemoSupabase,
  loadDemoSessions,
  type DemoSessionClient,
  type DemoSessionTokens,
  type DemoSessions,
} from '../lib/demo';
import type { MinimalSupabase } from '../lib/apply';

const UID_A = '00000000-0000-4000-8000-00000000000a';
const UID_B = '00000000-0000-4000-8000-00000000000b';

const SESSIONS: DemoSessions = {
  [UID_A]: { access_token: 'synthetic-access-a', refresh_token: 'synthetic-refresh-a' },
  [UID_B]: { access_token: 'synthetic-access-b', refresh_token: 'synthetic-refresh-b' },
};

// A fake anon client that RECORDS the tokens passed to setSession (no network).
// Its `auth` is a superset of MinimalSupabase['auth'] plus setSession — exactly
// the structural shape asDemoSupabase requires (and the real client satisfies).
function makeFakeClient(opts: { setSessionError?: string } = {}): DemoSessionClient & {
  setSessionCalls: DemoSessionTokens[];
} {
  const setSessionCalls: DemoSessionTokens[] = [];
  return {
    setSessionCalls,
    auth: {
      async getSession() {
        return { data: { session: null } };
      },
      async signInAnonymously() {
        return { data: { user: { id: UID_A } }, error: null };
      },
      async setSession(tokens: DemoSessionTokens) {
        setSessionCalls.push(tokens);
        return {
          error: opts.setSessionError ? { message: opts.setSessionError } : null,
        };
      },
    },
    from() {
      throw new Error('not used in these tests');
    },
  } as unknown as DemoSessionClient & { setSessionCalls: DemoSessionTokens[] };
}

describe('asDemoSupabase — production demo session swap', () => {
  it('signInAsUid restores THAT uid\'s seeded tokens via setSession', async () => {
    const client = makeFakeClient();
    const demo = asDemoSupabase(client, SESSIONS);

    await demo.signInAsUid(UID_B);

    expect(client.setSessionCalls).toHaveLength(1);
    expect(client.setSessionCalls[0]).toEqual({
      access_token: 'synthetic-access-b',
      refresh_token: 'synthetic-refresh-b',
    });
  });

  it('throws on an unknown uid (inconsistent seed) — never a silent no-op', async () => {
    const client = makeFakeClient();
    const demo = asDemoSupabase(client, SESSIONS);
    await expect(demo.signInAsUid('unknown-uid')).rejects.toThrow(
      /no seeded demo session/i,
    );
    expect(client.setSessionCalls).toHaveLength(0);
  });

  it('throws when setSession reports an error', async () => {
    const client = makeFakeClient({ setSessionError: 'token expired' });
    const demo = asDemoSupabase(client, SESSIONS);
    await expect(demo.signInAsUid(UID_A)).rejects.toThrow(/token expired/i);
  });

  it('the wrapped client satisfies isDemoSupabase; a bare client does not', () => {
    const client = makeFakeClient();
    const demo = asDemoSupabase(client, SESSIONS);
    expect(isDemoSupabase(demo)).toBe(true);

    // A bare anon client (no signInAsUid) is NOT a DemoSupabase → switcher disabled.
    const bare: MinimalSupabase = {
      auth: {
        async getSession() {
          return { data: { session: null } };
        },
        async signInAnonymously() {
          return { data: { user: { id: UID_A } }, error: null };
        },
      },
      from() {
        throw new Error('not used');
      },
    };
    expect(isDemoSupabase(bare)).toBe(false);
  });
});

describe('loadDemoSessions — VITE_DEMO_SESSIONS loader', () => {
  beforeEach(() => {
    // Hermetic baseline: clear any ambient VITE_DEMO_SESSIONS (a local apply/.env
    // sets it after a cloud re-seed) so the "unset" case is deterministic. Tests
    // that need a value stub their own below.
    vi.stubEnv('VITE_DEMO_SESSIONS', undefined as unknown as string);
  });
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('parses a valid env into the uid → tokens map', () => {
    vi.stubEnv('VITE_DEMO_SESSIONS', JSON.stringify(SESSIONS));
    expect(loadDemoSessions()).toEqual(SESSIONS);
  });

  it('returns {} when unset (no seed → switcher disabled, fail-safe)', () => {
    expect(loadDemoSessions()).toEqual({});
  });

  it('returns {} for malformed JSON', () => {
    vi.stubEnv('VITE_DEMO_SESSIONS', '{not json');
    expect(loadDemoSessions()).toEqual({});
  });

  it('returns {} when the value is a JSON array (wrong shape)', () => {
    vi.stubEnv('VITE_DEMO_SESSIONS', JSON.stringify([UID_A, UID_B]));
    expect(loadDemoSessions()).toEqual({});
  });

  it('drops entries missing string access_token / refresh_token', () => {
    vi.stubEnv(
      'VITE_DEMO_SESSIONS',
      JSON.stringify({
        [UID_A]: { access_token: 'ok', refresh_token: 'ok' },
        [UID_B]: { access_token: 'no-refresh' }, // malformed → dropped
        bad: { access_token: 1, refresh_token: 2 }, // non-string → dropped
      }),
    );
    expect(loadDemoSessions()).toEqual({
      [UID_A]: { access_token: 'ok', refresh_token: 'ok' },
    });
  });
});
