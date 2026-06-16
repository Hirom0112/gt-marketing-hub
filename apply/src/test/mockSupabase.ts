// A recording mock of the minimal Supabase surface the app uses. Captures every
// insert per table so tests can assert exact row/event shapes WITHOUT a network
// call. The acceptance tests use this to prove the INV-1/INV-6 guardrail: no
// value/content field and no child key is ever sent.

import type { MinimalSupabase } from '../lib/apply';

export interface RecordedInsert {
  table: string;
  rows: Record<string, unknown>[];
}

export interface MockSupabase extends MinimalSupabase {
  inserts: RecordedInsert[];
  rowsFor: (table: string) => Record<string, unknown>[];
  uid: string;
}

export function makeMockSupabase(
  opts: { uid?: string; failInsertOn?: string } = {},
): MockSupabase {
  const uid = opts.uid ?? '00000000-0000-4000-8000-000000000abc';
  const inserts: RecordedInsert[] = [];

  const mock: MockSupabase = {
    inserts,
    uid,
    rowsFor(table: string) {
      return inserts
        .filter((i) => i.table === table)
        .flatMap((i) => i.rows);
    },
    auth: {
      async getSession() {
        return { data: { session: null } };
      },
      async signInAnonymously() {
        return { data: { user: { id: uid } }, error: null };
      },
    },
    from(table: string) {
      return {
        async insert(rows: unknown) {
          const arr = (Array.isArray(rows) ? rows : [rows]) as Record<
            string,
            unknown
          >[];
          inserts.push({ table, rows: arr });
          if (opts.failInsertOn === table) {
            return { error: { message: `simulated failure on ${table}` } };
          }
          return { error: null };
        },
      };
    },
  };
  return mock;
}
