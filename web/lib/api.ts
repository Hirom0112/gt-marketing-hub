'use client';

// Real-backbone data layer. The Hub authenticates to the FastAPI backbone with a
// per-role demo-seat JWT (POST /auth/demo-token), caches it, and attaches it as a
// Bearer on every read. Calls go through /api/* which Next rewrites to the backend
// (GT_API_BASE_URL, default :8000). Every fetch fails soft → returns null, so a
// screen can fall back to its seed data when the backbone is unreachable (e.g. a
// static Vercel preview with no backend yet).

import type { Role } from './registry';

const tokenCache: Partial<Record<Role, string>> = {};

async function mintToken(role: Role): Promise<string | null> {
  if (tokenCache[role]) return tokenCache[role]!;
  try {
    const r = await fetch('/api/auth/demo-token', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ role }),
    });
    if (!r.ok) return null;
    const j = (await r.json()) as { access_token?: string };
    if (j.access_token) { tokenCache[role] = j.access_token; return j.access_token; }
    return null;
  } catch {
    return null;
  }
}

export async function apiGet<T>(path: string, role: Role): Promise<T | null> {
  const token = await mintToken(role);
  try {
    const r = await fetch(`/api${path}`, {
      headers: token ? { authorization: `Bearer ${token}` } : {},
      cache: 'no-store',
    });
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

// A write (PUT) through the same authed /api proxy. Returns the parsed body on 2xx,
// or null on any failure (auth, network, non-2xx) so callers can surface a soft error.
export async function apiPut<T>(path: string, role: Role, body: unknown): Promise<T | null> {
  const token = await mintToken(role);
  try {
    const r = await fetch(`/api${path}`, {
      method: 'PUT',
      headers: {
        'content-type': 'application/json',
        ...(token ? { authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
      cache: 'no-store',
    });
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

// A write (PATCH) through the same authed /api proxy. Mirrors apiPut with method
// PATCH (partial update). Returns the parsed body on 2xx, or null on any failure.
export async function apiPatch<T>(path: string, role: Role, body: unknown): Promise<T | null> {
  const token = await mintToken(role);
  try {
    const r = await fetch(`/api${path}`, {
      method: 'PATCH',
      headers: {
        'content-type': 'application/json',
        ...(token ? { authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
      cache: 'no-store',
    });
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

// A write (POST) through the same authed /api proxy. Mirrors apiPut with method
// POST. Returns the parsed body on 2xx, or null on any failure (auth, network,
// non-2xx) so callers can surface a soft error (raise + decide both go through here).
export async function apiPost<T>(path: string, role: Role, body: unknown): Promise<T | null> {
  const token = await mintToken(role);
  try {
    const r = await fetch(`/api${path}`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...(token ? { authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
      cache: 'no-store',
    });
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

// ---- typed shapes for the wired endpoints --------------------------------
// Matches GET /budget (backend app/api/budget.py). The Budget Tracker (Module 10)
// renders rows + flagged + rollup + per-workstream burn + the weekly burn series.
export type BudgetHealth = 'on_track' | 'watch' | 'at_risk';

export interface BudgetWorkstream {
  workstream: string;
  planned: number;
  committed: number;
  actual: number;
  remaining: number;
  variance: number; // (actual - planned) / planned, exact ratio
  flagged: boolean;
  health: BudgetHealth;
}
export interface BudgetBurnRow {
  workstream: string;
  planned: number;
  actual: number;
}
export interface BudgetBurnPoint {
  week_start: string; // "YYYY-MM-DD" (ISO-week Monday)
  cumulative_actual: number;
  cumulative_planned: number;
}
export interface BudgetRollup {
  total_planned: number;
  total_actual: number;
  total_remaining: number;
  total_usd: number;
  projected_burnout: string | null; // "YYYY-MM-DD" | null
}
export interface BudgetResponse {
  workstreams: BudgetWorkstream[];
  flagged: string[];
  rollup: BudgetRollup;
  burn: BudgetBurnRow[];
  burn_series: BudgetBurnPoint[];
}
