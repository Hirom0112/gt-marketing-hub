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
  UpdateBuilder,
} from '../lib/apply';

export interface RecordedInsert {
  table: string;
  rows: Record<string, unknown>[];
}

export interface RecordedDelete {
  table: string;
  filter: Record<string, unknown>;
}

export interface RecordedUpdate {
  table: string;
  values: Record<string, unknown>;
  filter: Record<string, unknown>;
}

export interface MockSupabase extends MinimalSupabase {
  inserts: RecordedInsert[];
  deletes: RecordedDelete[];
  updates: RecordedUpdate[];
  rowsFor: (table: string) => Record<string, unknown>[];
  uid: string;
}

export function makeMockSupabase(
  opts: {
    uid?: string;
    failInsertOn?: string;
    failDeleteOn?: string;
    failUpdateOn?: string;
    // R3 anon-resume: when true, getSession() returns a PERSISTED session (the
    // returning-family path) instead of null — mirrors persistSession: true.
    persistedSession?: boolean;
    // Pre-seed the row store (e.g. an application the persisted session owns).
    seed?: Record<string, Record<string, unknown>[]>;
  } = {},
): MockSupabase {
  const uid = opts.uid ?? '00000000-0000-4000-8000-000000000abc';
  const inserts: RecordedInsert[] = [];
  const deletes: RecordedDelete[] = [];
  const updates: RecordedUpdate[] = [];
  // The live row store per table — what a `select('*')` (RLS owner-scoped) sees.
  const store: Record<string, Record<string, unknown>[]> = {};
  for (const [table, rows] of Object.entries(opts.seed ?? {})) {
    store[table] = rows.map((r) => ({ ...r }));
  }

  const mock: MockSupabase = {
    inserts,
    deletes,
    updates,
    uid,
    rowsFor(table: string) {
      return store[table] ? [...store[table]!] : [];
    },
    auth: {
      async getSession() {
        // Persisted session ⇒ the returning-family resume path (R3); else null
        // ⇒ first visit, which triggers signInAnonymously below.
        return {
          data: {
            session: opts.persistedSession ? { user: { id: uid } } : null,
          },
        };
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
        update(values: unknown): UpdateBuilder {
          const vals = values as Record<string, unknown>;
          const filter: Record<string, unknown> = {};
          const builder: UpdateBuilder = {
            eq(column: string, value: unknown) {
              filter[column] = value;
              return builder;
            },
            then(onfulfilled) {
              updates.push({ table, values: { ...vals }, filter: { ...filter } });
              if (opts.failUpdateOn === table) {
                return Promise.resolve(
                  onfulfilled({
                    error: { message: `simulated update failure on ${table}` },
                  }),
                );
              }
              for (const row of store[table] ?? []) {
                if (Object.entries(filter).every(([k, v]) => row[k] === v)) {
                  Object.assign(row, vals);
                }
              }
              return Promise.resolve(onfulfilled({ error: null }));
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
