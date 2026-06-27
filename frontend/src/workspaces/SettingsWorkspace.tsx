import { Settings, ShieldCheck } from 'lucide-react';
import { Card, Chip } from '../ui';
import { apiBaseUrl, hubspotPortalId } from '../config';

// S14 Settings — a READ-ONLY config view. No tunables are edited here; this is a
// transparency surface that surfaces the real runtime values the cockpit is
// running with (API base URL, CRM mode, demo scenario, build info) plus the
// standing "live writes are off" guarantee (INV-2/INV-9, A-15/A-17). Values are
// read from `config`/env where available; v1 send modes are simulated by
// invariant, so they read as fixed facts, not editable knobs.

interface ConfigRow {
  label: string;
  value: string;
  note?: string;
  tone?: 'flow' | 'gate' | 'neutral';
}

// CRM_MODE is a server/build env var (TECH_STACK §5); the client never writes,
// so for the demo it is fixed at the committed default (simulate / recorded).
// Mirrored here as the recorded fact, not a live toggle.
const CRM_MODE = 'simulate';
const DEMO_SCENARIO = 'synthetic · seeded families (INV-1, no PII)';
const BUILD_CHANNEL = import.meta.env.MODE;

export default function SettingsWorkspace(): JSX.Element {
  const rows: ConfigRow[] = [
    {
      label: 'API base URL',
      value: apiBaseUrl,
      note: 'GT_API_BASE_URL (TECH_STACK §5.1)',
    },
    {
      label: 'CRM mode',
      value: `${CRM_MODE} (recorded)`,
      note: 'simulated adapter · no live HubSpot writes (INV-9, A-17)',
      tone: 'gate',
    },
    {
      label: 'HubSpot portal',
      value: hubspotPortalId,
      note: 'deep-link target for proof-of-capture',
    },
    {
      label: 'Demo scenario',
      value: DEMO_SCENARIO,
      tone: 'flow',
    },
    {
      label: 'Build channel',
      value: BUILD_CHANNEL,
      note: 'Vite mode',
    },
  ];

  return (
    <section
      aria-label="Settings workspace"
      data-testid="settings-workspace"
      style={{ display: 'grid', gap: 'var(--s-5)', maxWidth: 720 }}
    >
      <Card>
        <dl className="settings-list">
          {rows.map((row) => (
            <div className="settings-row" key={row.label}>
              <dt className="lab settings-row-label">{row.label}</dt>
              <dd className="settings-row-value">
                {row.tone ? (
                  <Chip tone={row.tone}>{row.value}</Chip>
                ) : (
                  <span className="mono">{row.value}</span>
                )}
                {row.note && (
                  <span className="settings-row-note">{row.note}</span>
                )}
              </dd>
            </div>
          ))}
        </dl>
      </Card>

      <Card>
        <div className="settings-guarantee">
          <ShieldCheck
            size={18}
            aria-hidden
            style={{ color: 'var(--flow)', flexShrink: 0 }}
          />
          <div>
            <p className="settings-guarantee-head">
              <Settings size={13} aria-hidden /> Live writes are off
            </p>
            <p className="settings-guarantee-body">
              Every outbound send and CRM write runs through a simulated adapter
              and is recorded server-side only · nothing leaves the cockpit this
              run. The deterministic core owns all writes; LLM output is a
              proposal that a human approves (INV-2). Tunables live in
              <span className="mono"> params/params.yaml</span>, not in this view.
            </p>
          </div>
        </div>
      </Card>
    </section>
  );
}
