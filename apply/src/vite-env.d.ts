/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_SUPABASE_URL?: string;
  readonly VITE_SUPABASE_ANON_KEY?: string;
  // Demo-only (MD): the cockpit URL for the pages-dropbox quick-jump, and the
  // seeded synthetic demo cohort the family-switcher lists (a JSON array of
  // {uid, familyId, label, hint}). Both are demo/seed-time config, synthetic-only.
  readonly VITE_COCKPIT_URL?: string;
  readonly VITE_DEMO_FAMILIES?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
