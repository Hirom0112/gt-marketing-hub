// Home live-data overlay — turns the static widget catalog into LIVE wire data.
//
// Home aggregates; it never recomputes a number. Each widget that has a real module
// endpoint gets its content REPLACED here with the live aggregate the owning module
// returns (Supabase / HubSpot / the Hub / the GA4 stood-in). Widgets with no live
// endpoint stay on their seed content, labelled SAMPLE. The status is honest per widget:
//   • 'live'      — a real backbone round-trip (Supabase app_form, live HubSpot, the Hub).
//   • 'simulated' — a real API round-trip, but the upstream source is a STOOD-IN (GA4).
//   • 'sample'    — static seed; no live endpoint (the widget keeps its catalog content).
//
// Every fetch fails soft (apiGet → null): a down endpoint just leaves its widgets on seed.

import type { Role } from './registry';
import { apiGet } from './api';
import type { WidgetContent } from './widgets';

export type LiveStatus = 'live' | 'simulated' | 'sample';

export interface HomeLive {
  content: Record<string, WidgetContent>; // widgetId → live content override
  status: Record<string, LiveStatus>; // widgetId → 'live' | 'simulated'
}

// ---- formatters -------------------------------------------------------------
const num = (n: number) => Math.round(n).toLocaleString('en-US');
const usdK = (n: number) => `$${Math.round(n / 1000)}K`;
const human = (t: string) => (t || '—').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
const barWidths = <T,>(rows: T[], val: (r: T) => number) => {
  const max = Math.max(1, ...rows.map(val));
  return (r: T) => Math.max(4, Math.round((100 * val(r)) / max));
};

// ---- minimal wire shapes (only the fields we read) --------------------------
interface WebsiteOverview {
  site_rollup: { total_sessions: number; total_pageviews: number };
  download_summary: { total_weekly: number; wow_delta_pct: number };
  top_downloads: { file_name: string }[];
  top_landing_pages: { page_path: string; pageviews: number; trend_pct: number }[];
}
interface AdmissionsOverview { top_objections: { theme: string; week_count: number }[] }
interface AdmissionsVoice { quote_of_week: { quote: string; theme: string } | null }
interface CrmOverview {
  lead_score_distribution: { bands: { label: string; count: number }[]; total: number } | null;
}
interface NurtureOverview {
  tiers: { tier: string; audience_size: number; reachability_pct: number }[];
  engagement_tier_mix: { clicked: number; opened: number; cold: number; total: number; reachable: number };
  sla_compliance_pct: number;
  pipeline_stage_distribution: { stage: string; count: number }[];
}
interface BudgetResponse {
  rollup: { total_planned: number; total_actual: number };
  workstreams: { workstream: string; actual: number }[];
}
interface ScorecardWeekly {
  metrics: { key: string; this_week: number; delta: number; target: number }[];
}
interface ContentPerformance {
  channels: { channel: string; reach: number; conversions: number; conversion_rate_pct: number }[];
}

export async function fetchHomeLive(role: Role): Promise<HomeLive> {
  const [web, adm, voice, crm, nur, bud, score, contentPerf] = await Promise.all([
    apiGet<WebsiteOverview>('/website/overview', role),
    apiGet<AdmissionsOverview>('/admissions/overview', role),
    apiGet<AdmissionsVoice>('/admissions/voice', role),
    apiGet<CrmOverview>('/crm/ops/overview', role),
    apiGet<NurtureOverview>('/nurture/overview', role),
    apiGet<BudgetResponse>('/budget', role),
    apiGet<ScorecardWeekly>('/scorecard/weekly', role),
    apiGet<ContentPerformance>('/content/performance', role),
  ]);

  const content: Record<string, WidgetContent> = {};
  const status: Record<string, LiveStatus> = {};
  const put = (id: string, c: WidgetContent, s: LiveStatus) => { content[id] = c; status[id] = s; };

  // ---- Website (GA4 stood-in → 'simulated') --------------------------------
  if (web?.site_rollup) {
    put('website-sessions', {
      kind: 'stat',
      value: num(web.site_rollup.total_sessions),
      delta: 'both sites',
      deltaColor: 'var(--ink-3)',
      sub: `${num(web.site_rollup.total_pageviews)} pageviews this week`,
    }, 'simulated');

    put('top-landing-pages', {
      kind: 'list',
      items: web.top_landing_pages.slice(0, 3).map(
        (p) => `${p.page_path} — ${num(p.pageviews)} views (${p.trend_pct >= 0 ? '▲' : '▼'}${Math.abs(p.trend_pct)}%)`,
      ),
    }, 'simulated');

    const wow = web.download_summary.wow_delta_pct;
    put('pdf-downloads', {
      kind: 'stat',
      value: num(web.download_summary.total_weekly),
      delta: `${wow >= 0 ? '▲' : '▼'}${Math.abs(wow)}% w/w`,
      deltaColor: wow >= 0 ? 'var(--ok)' : 'var(--warn)',
      sub: web.top_downloads[0] ? `top: ${web.top_downloads[0].file_name}` : 'PDF + resource downloads',
    }, 'simulated');
  }

  // ---- Admissions (live) ---------------------------------------------------
  if (adm?.top_objections) {
    put('top-objections', {
      kind: 'list',
      items: adm.top_objections.slice(0, 3).map((o) => `“${human(o.theme)}” — ${o.week_count} this week`),
    }, 'live');
  }
  if (voice?.quote_of_week) {
    put('family-quote', {
      kind: 'list',
      items: [`“${voice.quote_of_week.quote}” — on ${human(voice.quote_of_week.theme)}`],
    }, 'live');
  }

  // ---- CRM Ops · live HubSpot lead-score histogram -------------------------
  if (crm?.lead_score_distribution?.bands) {
    const bands = crm.lead_score_distribution.bands;
    const w = barWidths(bands, (b) => b.count);
    put('lead-score-dist', {
      kind: 'bars',
      rows: bands.map((b) => ({ name: b.label, pct: num(b.count), width: w(b) })),
    }, 'live');
  }

  // ---- Nurture (live HubSpot + Supabase) -----------------------------------
  if (nur?.engagement_tier_mix) {
    const m = nur.engagement_tier_mix;
    const t = Math.max(1, m.total);
    put('engagement-tier-mix', {
      kind: 'split',
      segs: [
        { w: Math.round((100 * m.clicked) / t), label: 'Clicked', value: `${Math.round((100 * m.clicked) / t)}%`, color: 'var(--gold)' },
        { w: Math.round((100 * m.opened) / t), label: 'Opened', value: `${Math.round((100 * m.opened) / t)}%`, color: 'var(--gold)', textColor: 'var(--ink)' },
        { w: Math.round((100 * m.cold) / t), label: 'Cold', value: `${Math.round((100 * m.cold) / t)}%`, color: 'var(--broken)' },
      ],
      sub: `${num(m.reachable)} of ${num(m.total)} reachable`,
    }, 'live');
  }
  if (nur?.tiers) {
    put('t1t2t3-counts', {
      kind: 'tiers',
      items: nur.tiers.map((t) => ({ n: num(t.audience_size), label: `${t.tier} · ${t.reachability_pct}% reach` })),
      sub: 'Audience size + reachability by tier',
    }, 'live');
  }
  if (nur?.pipeline_stage_distribution) {
    const rows = nur.pipeline_stage_distribution;
    const w = barWidths(rows, (r) => r.count);
    put('funnel-stages', {
      kind: 'bars',
      rows: rows.map((r) => ({ name: human(r.stage), pct: num(r.count), width: w(r) })),
    }, 'live');
  }
  if (typeof nur?.sla_compliance_pct === 'number') {
    const pct = nur.sla_compliance_pct;
    put('sla-24h', {
      kind: 'progress',
      value: `${pct}%`,
      pct,
      color: pct >= 90 ? 'var(--ok)' : 'var(--warn)',
      sub: 'target 90% · 24-hr follow-up',
    }, 'live');
  }

  // ---- Budget (the Hub) ----------------------------------------------------
  if (bud?.rollup) {
    const { total_planned, total_actual } = bud.rollup;
    const pct = total_planned > 0 ? Math.round((100 * total_actual) / total_planned) : 0;
    put('budget-burn', {
      kind: 'progress',
      value: `${usdK(total_actual)} / ${usdK(total_planned)}`,
      pct,
      color: 'var(--gold)',
      sub: `${pct}% of the $365K plan spent`,
    }, 'live');

    const w = barWidths(bud.workstreams, (x) => x.actual);
    put('spend-by-workstream', {
      kind: 'bars',
      rows: bud.workstreams.map((x) => ({ name: human(x.workstream), pct: usdK(x.actual), width: w(x) })),
    }, 'live');
  }

  // ---- Scorecard (Supabase app_form funnel + Stripe ledger) ----------------
  if (score?.metrics) {
    const by = (k: string) => score.metrics.find((m) => m.key === k);
    const ap = by('applicants');
    if (ap) {
      put('applicants-total', {
        kind: 'stat',
        value: num(ap.this_week),
        delta: `${ap.delta >= 0 ? '▲' : '▼'}${num(Math.abs(ap.delta))} w/w`,
        deltaColor: ap.delta >= 0 ? 'var(--ok)' : 'var(--warn)',
        sub: 'Supabase app_form funnel (all stages)',
      }, 'live');
    }
    const dep = by('deposits');
    if (dep) {
      const target = dep.target || 180;
      const pct = target > 0 ? Math.round((100 * dep.this_week) / target) : 0;
      put('deposits-vs-goal', {
        kind: 'progress',
        value: `${num(dep.this_week)} / ${num(target)}`,
        pct,
        color: 'var(--gold)',
        sub: `${pct}% of the Fall goal · Stripe deposit ledger`,
      }, 'live');
    }
  }

  // ---- Conversion by channel (Content perf — Google Sheet + computed) -------
  if (contentPerf?.channels?.length) {
    const top = [...contentPerf.channels].sort((a, b) => b.conversion_rate_pct - a.conversion_rate_pct).slice(0, 5);
    const w = barWidths(top, (c) => c.conversion_rate_pct);
    put('conversion-by-channel', {
      kind: 'bars',
      rows: top.map((c) => ({ name: human(c.channel), pct: `${c.conversion_rate_pct}%`, width: w(c) })),
    }, 'live');
  }

  return { content, status };
}
