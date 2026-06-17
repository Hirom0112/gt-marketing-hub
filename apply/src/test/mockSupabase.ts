// A recording mock of the minimal Supabase surface the app uses. Captures every
// insert/select/delete per table so tests can assert exact row/event shapes
// WITHOUT a network call. The acceptance tests use this to prove the INV-1/INV-6
// guardrail: no value/content field and no child key is ever sent.
//
// RLS note: the live anon client is auto-scoped to auth.uid() by the owner-scoped
// policies, so a `select('*')` returns only the session's own rows. This mock has
// a single session (one uid), so every recorded row IS the session's — `select`
// returns the live store for the table and `delete` removes matching rows from it.

import type {
  DeleteBuilder,
  MinimalSupabase,
  SelectBuilder,
} from '../lib/apply';

export interface RecordedInsert {
  table: string;
  rows: Record<string, unknown>[];
}

export interface RecordedDelete {
  table: string;
  filter: Record<string, unknown>;
}

export interface MockSupabase extends MinimalSupabase {
  inserts: RecordedInsert[];
  deletes: RecordedDelete[];
  rowsFor: (table: string) => Record<string, unknown>[];
  uid: string;
}

export function makeMockSupabase(
  opts: { uid?: string; failInsertOn?: string; failDeleteOn?: string } = {},
): MockSupabase {
  const uid = opts.uid ?? '00000000-0000-4000-8000-000000000abc';
  const inserts: RecordedInsert[] = [];
  const deletes: RecordedDelete[] = [];
  // The live row store per table — what a `select('*')` (RLS owner-scoped) sees.
  const store: Record<string, Record<string, unknown>[]> = {};

  const mock: MockSupabase = {
    inserts,
    deletes,
    uid,
    rowsFor(table: string) {
      return store[table] ? [...store[table]!] : [];
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
          (store[table] ??= []).push(...arr);
          return { error: null };
        },
        select(): SelectBuilder {
          const filters: Record<string, unknown> = {};
          const builder: SelectBuilder = {
            eq(column: string, value: unknown) {
              filters[column] = value;
              return builder;
            },
            then(onfulfilled) {
              const rows = (store[table] ?? []).filter((r) =>
                Object.entries(filters).every(([k, v]) => r[k] === v),
              );
              return Promise.resolve(onfulfilled({ data: rows, error: null }));
            },
          };
          return builder;
        },
        delete(): DeleteBuilder {
          const filter: Record<string, unknown> = {};
          const builder: DeleteBuilder = {
            eq(column: string, value: unknown) {
              filter[column] = value;
              return builder;
            },
            then(onfulfilled) {
              deletes.push({ table, filter: { ...filter } });
              if (opts.failDeleteOn === table) {
                return Promise.resolve(
                  onfulfilled({
                    error: { message: `simulated delete failure on ${table}` },
                  }),
                );
              }
              store[table] = (store[table] ?? []).filter(
                (r) =>
                  !Object.entries(filter).every(([k, v]) => r[k] === v),
              );
              return Promise.resolve(onfulfilled({ error: null }));
            },
          };
          return builder;
        },
      };
    },
  };
  return mock;
}
