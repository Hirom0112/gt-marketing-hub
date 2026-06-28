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

// ---- typed shapes for the wired endpoints --------------------------------
export interface BudgetWorkstream {
  workstream: string;
  planned: number;
  committed: number;
  actual: number;
  remaining: number;
  variance: number;
  flagged: boolean;
}
export interface BudgetResponse {
  workstreams: BudgetWorkstream[];
  total?: { planned: number; committed: number; actual: number; remaining: number };
}
