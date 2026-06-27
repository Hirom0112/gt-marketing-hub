import { useState } from 'react';
import { Download, Globe, Route, Table2 } from 'lucide-react';
import {
  Card,
  Chip,
  KpiCard,
  PlaceholderBadge,
  TabBar,
  type TabItem,
} from '../ui';

// Module 13 — Website & Digital Analytics (GT Marketing Hub spec §3, Module 13).
// GA4 across gt.school + anywhere.gt.school. The brief classes this as "mostly an
// integration + viz module; easy to stub with seeded data" and requires that
// stood-in sources be LABELED honestly. So v1 is a seeded GA4 stand-in behind a
// visible "SIMULATED GA4" marker (INV-9 honesty): real shapes, no live GA4 SDK
// (which would also blow the dependency budget — live is the documented v2 step).

type Tab = 'overview' | 'subpages' | 'sources' | 'downloads';

const TABS: ReadonlyArray<TabItem<Tab>> = [
  { key: 'overview', label: 'Overview', icon: Globe },
  { key: 'subpages', label: 'Subpages', icon: Table2 },
  { key: 'sources', label: 'Traffic sources', icon: Route },
  { key: 'downloads', label: 'Downloads', icon: Download },
];

// --- Seeded GA4 stand-in (two sites, realistic spread) ----------------------
interface SiteRow {
  site: string;
  sessions: number;
  bounce: number; // %
  avgDurationSec: number;
}
const SITES: readonly SiteRow[] = [
  { site: 'gt.school', sessions: 18420, bounce: 41, avgDurationSec: 134 },
  { site: 'anywhere.gt.school', sessions: 9260, bounce: 48, avgDurationSec: 96 },
];
const TOTAL_SESSIONS = SITES.reduce((a, s) => a + s.sessions, 0);
const NEW_VISITOR_PCT = 63;

interface PageRow {
  path: string;
  site: string;
  type: 'landing' | 'blog' | 'resource' | 'form' | 'about';
  pageviews: number;
  unique: number;
  avgTimeSec: number;
  bounce: number;
  conversions: number;
}
const PAGES: readonly PageRow[] = [
  { path: '/apply', site: 'gt.school', type: 'form', pageviews: 5120, unique: 4380, avgTimeSec: 210, bounce: 22, conversions: 612 },
  { path: '/', site: 'gt.school', type: 'landing', pageviews: 8940, unique: 7110, avgTimeSec: 88, bounce: 44, conversions: 0 },
  { path: '/gifted-challenge', site: 'gt.school', type: 'landing', pageviews: 4310, unique: 3920, avgTimeSec: 156, bounce: 31, conversions: 388 },
  { path: '/tuition-and-esa', site: 'gt.school', type: 'blog', pageviews: 2680, unique: 2240, avgTimeSec: 198, bounce: 38, conversions: 41 },
  { path: '/summer-camp', site: 'anywhere.gt.school', type: 'landing', pageviews: 3150, unique: 2870, avgTimeSec: 142, bounce: 36, conversions: 119 },
  { path: '/parent-playbook', site: 'anywhere.gt.school', type: 'resource', pageviews: 1990, unique: 1760, avgTimeSec: 233, bounce: 29, conversions: 0 },
  { path: '/about', site: 'anywhere.gt.school', type: 'about', pageviews: 1240, unique: 1080, avgTimeSec: 64, bounce: 55, conversions: 0 },
];

interface SourceRow {
  source: string;
  sessions: number;
  detail?: string;
}
const SOURCES: readonly SourceRow[] = [
  { source: 'Organic search', sessions: 9870 },
  { source: 'Social', sessions: 7240, detail: 'X 58% · Facebook 27% · Instagram 15%' },
  { source: 'Direct', sessions: 4980 },
  { source: 'Email', sessions: 3110, detail: 'from marketing sequences' },
  { source: 'Referral', sessions: 1480 },
  { source: 'UTM campaigns', sessions: 1000, detail: 'feeds CRM Ops attribution chain' },
];
const SOURCE_TOTAL = SOURCES.reduce((a, s) => a + s.sessions, 0);

interface DownloadRow {
  file: string;
  count: number;
  referrer: string;
  source: string;
}
const DOWNLOADS: readonly DownloadRow[] = [
  { file: 'GT-Parent-Playbook.pdf', count: 1820, referrer: '/parent-playbook', source: 'organic' },
  { file: 'Gifted-Challenge-Sample.pdf', count: 1240, referrer: '/gifted-challenge', source: 'social' },
  { file: 'Tuition-ESA-Guide.pdf', count: 760, referrer: '/tuition-and-esa', source: 'email' },
  { file: 'Summer-Camp-Brochure.pdf', count: 540, referrer: '/summer-camp', source: 'direct' },
];

function pct(n: number, total: number): number {
  return total === 0 ? 0 : Math.round((n / total) * 100);
}

function MiniBar({ value }: { value: number }): JSX.Element {
  return (
    <div
      style={{
        height: 6,
        width: '100%',
        background: 'var(--line-2)',
        borderRadius: 999,
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          height: '100%',
          width: `${value}%`,
          background: 'var(--flow)',
        }}
      />
    </div>
  );
}

const cellHead = {
  textAlign: 'left',
  padding: 'var(--s-2) var(--s-3)',
  fontSize: 11,
  letterSpacing: '0.04em',
  color: 'var(--ink-soft)',
  borderBottom: '1px solid var(--line)',
} as const;
const cell = {
  padding: 'var(--s-2) var(--s-3)',
  borderBottom: '1px solid var(--line-2)',
  fontSize: 13,
} as const;

export default function WebsiteAnalyticsWorkspace(): JSX.Element {
  const [tab, setTab] = useState<Tab>('overview');

  return (
    <section
      aria-label="Website analytics workspace"
      data-testid="website-analytics-workspace"
      style={{ display: 'grid', gap: 'var(--s-5)', maxWidth: 980 }}
    >
      <header style={{ display: 'grid', gap: 'var(--s-2)' }}>
        <div
          style={{ display: 'flex', alignItems: 'center', gap: 'var(--s-3)' }}
        >
          <h1 style={{ margin: 0 }}>Website & Digital Analytics</h1>
          <PlaceholderBadge label="SIMULATED GA4" />
        </div>
        <p style={{ margin: 0, color: 'var(--ink-soft)', maxWidth: '64ch' }}>
          GA4 across gt.school and anywhere.gt.school. Seeded stand-in data, real
          shapes. UTM-tagged traffic feeds the CRM Ops attribution chain.
        </p>
      </header>

      <TabBar tabs={TABS} active={tab} onSelect={setTab} ariaLabel="Analytics views" />

      {tab === 'overview' && <Overview />}
      {tab === 'subpages' && <Subpages />}
      {tab === 'sources' && <Sources />}
      {tab === 'downloads' && <Downloads />}
    </section>
  );
}

function Overview(): JSX.Element {
  const avg = Math.round(
    SITES.reduce((a, s) => a + s.avgDurationSec * s.sessions, 0) /
      TOTAL_SESSIONS,
  );
  const bounce = Math.round(
    SITES.reduce((a, s) => a + s.bounce * s.sessions, 0) / TOTAL_SESSIONS,
  );
  const topLanding = [...PAGES]
    .sort((a, b) => b.pageviews - a.pageviews)
    .slice(0, 5);

  return (
    <div style={{ display: 'grid', gap: 'var(--s-5)' }}>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))',
          gap: 'var(--s-3)',
        }}
      >
        <KpiCard label="Sessions this week" value={TOTAL_SESSIONS.toLocaleString()} note="both sites" />
        <KpiCard label="Avg session" value={`${Math.floor(avg / 60)}m ${avg % 60}s`} />
        <KpiCard label="Bounce rate" value={`${bounce}%`} tone={bounce > 50 ? 'signal' : 'neutral'} />
        <KpiCard label="New visitors" value={`${NEW_VISITOR_PCT}%`} note={`${100 - NEW_VISITOR_PCT}% returning`} />
      </div>

      <Card>
        <p className="mono" style={{ fontSize: 11, color: 'var(--ink-soft)', margin: '0 0 var(--s-3)' }}>
          SESSIONS BY SITE
        </p>
        <div style={{ display: 'grid', gap: 'var(--s-3)' }}>
          {SITES.map((s) => (
            <div key={s.site} style={{ display: 'grid', gap: 6 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13 }}>
                <span style={{ fontWeight: 600 }}>{s.site}</span>
                <span className="mono" style={{ color: 'var(--ink-soft)' }}>
                  {s.sessions.toLocaleString()} · {pct(s.sessions, TOTAL_SESSIONS)}%
                </span>
              </div>
              <MiniBar value={pct(s.sessions, TOTAL_SESSIONS)} />
            </div>
          ))}
        </div>
      </Card>

      <Card>
        <p className="mono" style={{ fontSize: 11, color: 'var(--ink-soft)', margin: '0 0 var(--s-2)' }}>
          TOP LANDING PAGES
        </p>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={cellHead}>Page</th>
              <th style={{ ...cellHead, textAlign: 'right' }}>Pageviews</th>
              <th style={{ ...cellHead, textAlign: 'right' }}>Conversions</th>
            </tr>
          </thead>
          <tbody>
            {topLanding.map((p) => (
              <tr key={p.path}>
                <td style={cell}>
                  <span className="mono">{p.path}</span>{' '}
                  <span style={{ color: 'var(--ink-soft)', fontSize: 11 }}>{p.site}</span>
                </td>
                <td style={{ ...cell, textAlign: 'right' }} className="mono">{p.pageviews.toLocaleString()}</td>
                <td style={{ ...cell, textAlign: 'right' }} className="mono">{p.conversions || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function Subpages(): JSX.Element {
  return (
    <Card>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            <th style={cellHead}>Page</th>
            <th style={cellHead}>Type</th>
            <th style={{ ...cellHead, textAlign: 'right' }}>Views</th>
            <th style={{ ...cellHead, textAlign: 'right' }}>Unique</th>
            <th style={{ ...cellHead, textAlign: 'right' }}>Avg time</th>
            <th style={{ ...cellHead, textAlign: 'right' }}>Bounce</th>
            <th style={{ ...cellHead, textAlign: 'right' }}>Conv.</th>
          </tr>
        </thead>
        <tbody>
          {PAGES.map((p) => (
            <tr key={p.path}>
              <td style={cell}>
                <span className="mono">{p.path}</span>{' '}
                <span style={{ color: 'var(--ink-soft)', fontSize: 11 }}>{p.site}</span>
              </td>
              <td style={cell}><Chip tone="neutral">{p.type}</Chip></td>
              <td style={{ ...cell, textAlign: 'right' }} className="mono">{p.pageviews.toLocaleString()}</td>
              <td style={{ ...cell, textAlign: 'right' }} className="mono">{p.unique.toLocaleString()}</td>
              <td style={{ ...cell, textAlign: 'right' }} className="mono">{Math.floor(p.avgTimeSec / 60)}m {p.avgTimeSec % 60}s</td>
              <td style={{ ...cell, textAlign: 'right' }} className="mono">{p.bounce}%</td>
              <td style={{ ...cell, textAlign: 'right' }} className="mono">{p.conversions || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function Sources(): JSX.Element {
  return (
    <Card>
      <div style={{ display: 'grid', gap: 'var(--s-4)' }}>
        {SOURCES.map((s) => (
          <div key={s.source} style={{ display: 'grid', gap: 6 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', fontSize: 13 }}>
              <span style={{ fontWeight: 600 }}>
                {s.source}{' '}
                {s.detail ? (
                  <span style={{ fontWeight: 400, color: 'var(--ink-soft)', fontSize: 12 }}>
                    {s.detail}
                  </span>
                ) : null}
              </span>
              <span className="mono" style={{ color: 'var(--ink-soft)' }}>
                {s.sessions.toLocaleString()} · {pct(s.sessions, SOURCE_TOTAL)}%
              </span>
            </div>
            <MiniBar value={pct(s.sessions, SOURCE_TOTAL)} />
          </div>
        ))}
      </div>
    </Card>
  );
}

function Downloads(): JSX.Element {
  return (
    <Card>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr>
            <th style={cellHead}>File</th>
            <th style={cellHead}>Referring page</th>
            <th style={cellHead}>Source</th>
            <th style={{ ...cellHead, textAlign: 'right' }}>Downloads</th>
          </tr>
        </thead>
        <tbody>
          {[...DOWNLOADS].sort((a, b) => b.count - a.count).map((d) => (
            <tr key={d.file}>
              <td style={cell}><span className="mono">{d.file}</span></td>
              <td style={cell}><span className="mono" style={{ color: 'var(--ink-soft)' }}>{d.referrer}</span></td>
              <td style={cell}><Chip tone="neutral">{d.source}</Chip></td>
              <td style={{ ...cell, textAlign: 'right' }} className="mono">{d.count.toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
