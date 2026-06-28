'use client';

// Admissions & Voice of Customer (Module 9) — the listening post.
//   • Objection log: themed, frequency-counted, trended verbatims off HubSpot
//     Conversations + BDR calls + SMS + forms.
//   • Objection→content bridge: the top objections auto-stub a content brief in
//     the Content module (Module 3); bridge hit-rate tracks briefs→published.
//   • Voice of families: qualitative feed + rotating quote-of-the-week + a
//     positive/neutral/negative sentiment trend (+47 NPS, n=212).
//   • Feedback→marketing loop: "marketing needs to know X" items, categorized,
//     with a 7-day closure rate; hot families push to Nurture + Decision Queue.
// Ported faithfully from the design prototype; all data inlined as typed consts.

import { moduleById } from '@/lib/registry';
import { TabBar } from '@/components/TabBar';

const MONO = 'JetBrains Mono';
const ARCHIVO = 'Fraunces';

// ---- Types -----------------------------------------------------------------
interface TopStat {
  label: string;
  value: string;
  valueSub?: string;
  valueColor: string;
  note: string;
}
type Trend = 'up' | 'flat' | 'down';
interface Objection {
  theme: string;
  freq: number;
  trend: Trend;
  source: string;
  quote: string;
}
interface BriefStub {
  theme: string;
  example: string;
  angle: string;
  persona: string;
  urgency: string;
  uc: string; // urgency text color token
  ubg: string; // urgency bg token
}
interface VoiceQuote {
  quote: string;
  who: string;
  tone: 'pos' | 'neu' | 'neg';
}
interface SentimentWeek {
  week: string;
  pos: number;
  neu: number;
  neg: number;
}
interface FeedbackItem {
  cat: string;
  note: string;
  status: string;
  actioned: boolean;
  c: string; // category text color token
  bg: string; // category bg token
}

// ---- Seed data -------------------------------------------------------------
const TOP_STATS: TopStat[] = [
  {
    label: 'OBJECTIONS LOGGED · WK',
    value: '47',
    valueColor: 'var(--ink)',
    note: 'top themes: cost · accreditation',
  },
  {
    label: 'FAMILY SENTIMENT',
    value: '+47',
    valueSub: 'NPS',
    valueColor: 'var(--ok)',
    note: 'n=212 · ↑6 vs last month',
  },
  {
    label: 'CONTENT-BRIDGE HIT RATE',
    value: '68%',
    valueColor: 'var(--gold)',
    note: 'briefs stubbed → published',
  },
  {
    label: 'OBJECTION → RESOLUTION',
    value: '3.4d',
    valueColor: 'var(--warn)',
    note: 'median · target 2d',
  },
];

// Trend arrow + color encode direction: ↑ rising = --signal, → stable = --ink-3,
// ↓ falling = --ok (a falling objection is a good thing).
const TREND_META: Record<Trend, { glyph: string; color: string }> = {
  up: { glyph: '↑', color: 'var(--signal)' },
  flat: { glyph: '→', color: 'var(--ink-3)' },
  down: { glyph: '↓', color: 'var(--ok)' },
};

const OBJECTIONS: Objection[] = [
  { theme: 'cost', freq: 14, trend: 'up', source: 'BDR call', quote: '"$10k a year before the ESA even clears — we can\'t float that."' },
  { theme: 'accreditation', freq: 11, trend: 'up', source: 'form', quote: '"Is this an accredited school, or does my kid end up with no real diploma?"' },
  { theme: 'is my kid gifted enough', freq: 8, trend: 'flat', source: 'SMS', quote: '"He\'s bright but not a prodigy — is Alpha only for the genius kids?"' },
  { theme: 'scheduling', freq: 6, trend: 'down', source: 'event', quote: '"When does the day actually start? I work and can\'t do a 7am drop."' },
  { theme: 'curriculum', freq: 4, trend: 'flat', source: 'BDR call', quote: '"What do they actually learn if an app teaches the academics?"' },
  { theme: 'social', freq: 3, trend: 'down', source: 'SMS', quote: '"I worry she\'ll be isolated staring at a screen all day."' },
  { theme: 'tech requirements', freq: 1, trend: 'flat', source: 'form', quote: '"Do we have to buy the iPad, or is the device provided?"' },
];

const BRIEF_STUBS: BriefStub[] = [
  {
    theme: 'cost',
    example: '"$10k before the ESA clears — we can\'t float that."',
    angle: 'TEFA timeline explainer: when funds land, the 25/25/50 installment cadence, bridge options.',
    persona: 'ESA-planned · out-of-pocket-anxious',
    urgency: 'HIGH',
    uc: 'var(--signal)',
    ubg: 'var(--signal-soft)',
  },
  {
    theme: 'accreditation',
    example: '"Does my kid end up with no real diploma?"',
    angle: 'Accreditation + transcript/diploma proof piece; alumni placement receipts.',
    persona: 'first-time · diploma-skeptic',
    urgency: 'HIGH',
    uc: 'var(--signal)',
    ubg: 'var(--signal-soft)',
  },
  {
    theme: 'is my kid gifted enough',
    example: '"Is Alpha only for the genius kids?"',
    angle: '"Mastery-based, not gifted-gated" — show the median learner journey, not the outlier.',
    persona: 'bright-but-not-prodigy parent',
    urgency: 'MED',
    uc: 'var(--gold)',
    ubg: 'var(--gold-soft)',
  },
];

const VOICE_QUOTES: VoiceQuote[] = [
  { quote: 'The 2-hour academic core gave my daughter her afternoons back — she started a pottery business.', who: 'Parent · K–2 cohort · Austin', tone: 'pos' },
  { quote: 'Loved the tour but nobody followed up for nine days. I\'d already half-moved on.', who: 'Tour attendee · no-show re-engage', tone: 'neg' },
  { quote: 'Still not clear how the guides differ from teachers. Explain that and I\'m sold.', who: 'Form inquiry · curriculum question', tone: 'neu' },
  { quote: 'My son went from hating school to asking to do extra. That alone is worth it.', who: 'Enrolled family · 3rd–5th', tone: 'pos' },
  { quote: 'The ESA paperwork felt heavier than enrolling itself. A checklist would have saved me.', who: 'ESA-planned · committed', tone: 'neg' },
];

const QUOTE_OF_WEEK: VoiceQuote = {
  quote: 'I came in a skeptic about "an app teaching my kid." I left realizing the app is the floor, and the guides build everything on top of it. That reframed the whole thing for me.',
  who: 'Parent · shadow-day visitor · converted to apply',
  tone: 'pos',
};

const SENTIMENT_TREND: SentimentWeek[] = [
  { week: 'W-3', pos: 58, neu: 28, neg: 14 },
  { week: 'W-2', pos: 61, neu: 26, neg: 13 },
  { week: 'W-1', pos: 64, neu: 24, neg: 12 },
  { week: 'This wk', pos: 67, neu: 22, neg: 11 },
];

const FEEDBACK: FeedbackItem[] = [
  { cat: 'MESSAGING GAP', note: 'Families don\'t connect "2-hour learning" to academic rigor — reads as "less school".', status: 'actioned · brief stubbed', actioned: true, c: 'var(--signal)', bg: 'var(--signal-soft)' },
  { cat: 'PERSONA MISMATCH', note: '"Gifted enough?" recurs from mid-tier learners — our hero copy over-indexes on prodigies.', status: 'in review', actioned: false, c: 'var(--warn)', bg: 'var(--warn-soft)' },
  { cat: 'OBJECTION PATTERN', note: 'Accreditation questions up 38% since the competitor\'s diploma campaign.', status: 'actioned · Content + Decision', actioned: true, c: 'var(--gold)', bg: 'var(--gold-soft)' },
  { cat: 'POSITIVE SIGNAL', note: '"Afternoons back" framing lands hard with working parents — under-used in ads.', status: 'actioned · ad angle live', actioned: true, c: 'var(--ok)', bg: 'var(--ok-soft)' },
  { cat: 'URGENT', note: '3 high-intent families stalled on ESA paperwork confusion — risk of churn this week.', status: 'pushed → Nurture + Decision Queue', actioned: false, c: 'var(--broken)', bg: 'var(--warn-soft)' },
];

// ---- Component -------------------------------------------------------------
export function AdmissionsModule() {
  const def = moduleById('admissions')!;

  const closed = FEEDBACK.filter((f) => f.actioned).length;
  const closureRate = Math.round((closed / FEEDBACK.length) * 100);

  return (
    <>
      <TabBar tabs={def.tabs} />
      <section className="scr" style={{ padding: '20px 22px 40px' }}>
        {/* Overview stat row */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 14 }}>
          {TOP_STATS.map((s, i) => {
            const hero = i === 1;
            return (
              <div
                key={s.label}
                style={{
                  border: `1px solid ${hero ? 'var(--ok)' : 'var(--line-2)'}`,
                  background: hero ? 'var(--ok-soft)' : 'var(--card)',
                  padding: 13,
                }}
              >
                <div
                  style={{
                    fontFamily: MONO,
                    fontSize: 9,
                    letterSpacing: '.4px',
                    color: hero ? 'var(--ok)' : 'var(--ink-3)',
                    fontWeight: hero ? 600 : 400,
                  }}
                >
                  {s.label}
                </div>
                <div style={{ fontFamily: 'Fraunces', fontWeight: 600, fontSize: 26, lineHeight: 1.05, color: s.valueColor, marginTop: 6 }}>
                  {s.value}
                  {s.valueSub && <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 600, color: 'var(--ink-3)' }}> {s.valueSub}</span>}
                </div>
                <div style={{ fontSize: 10, color: 'var(--ink-2)', marginTop: 3 }}>{s.note}</div>
              </div>
            );
          })}
        </div>

        {/* OBJECTION LOG */}
        <div style={{ border: '1px solid var(--ink)', background: 'var(--card)', marginBottom: 14 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
            <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Objection log</div>
            <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>HubSpot Conv. + BDR + SMS + forms · sort: frequency ↓</span>
          </div>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1.5fr .5fr .5fr .8fr 2.6fr',
              fontFamily: MONO,
              fontSize: 8.5,
              letterSpacing: '.3px',
              color: 'var(--ink-3)',
              padding: '8px 16px',
              borderBottom: '1px solid var(--line-2)',
              fontWeight: 600,
            }}
          >
            <div>THEME</div>
            <div style={{ textAlign: 'right' }}>FREQ</div>
            <div style={{ textAlign: 'center' }}>TREND</div>
            <div>SOURCE</div>
            <div>EXAMPLE VERBATIM</div>
          </div>
          {OBJECTIONS.map((o) => {
            const tm = TREND_META[o.trend];
            return (
              <div
                key={o.theme}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '1.5fr .5fr .5fr .8fr 2.6fr',
                  alignItems: 'center',
                  padding: '9px 16px',
                  borderBottom: '1px solid var(--line)',
                }}
              >
                <div>
                  <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', borderRadius: 2, background: 'var(--accent-soft)', color: 'var(--ink-2)', whiteSpace: 'nowrap' }}>{o.theme}</span>
                </div>
                <div style={{ textAlign: 'right', fontFamily: MONO, fontSize: 12, fontWeight: 600, color: 'var(--ink)' }}>{o.freq}</div>
                <div style={{ textAlign: 'center', fontFamily: MONO, fontSize: 14, fontWeight: 700, color: tm.color }}>{tm.glyph}</div>
                <div style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>{o.source}</div>
                <div style={{ fontSize: 10.5, color: 'var(--ink-2)', fontStyle: 'italic', lineHeight: 1.4 }}>{o.quote}</div>
              </div>
            );
          })}
          <div style={{ padding: '8px 16px', fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>
            ↑ rising · → stable · ↓ falling · the top-frequency rising themes auto-stub a Content brief (below)
          </div>
        </div>

        {/* OBJECTION → CONTENT BRIDGE */}
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', marginBottom: 14 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
            <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Objection → content bridge</div>
            <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>top objections auto-stub a brief in Content (Module 3) · hit-rate 68% briefs → published</span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 0 }}>
            {BRIEF_STUBS.map((b, i) => (
              <div
                key={b.theme}
                style={{
                  padding: 14,
                  borderRight: i < BRIEF_STUBS.length - 1 ? '1px solid var(--line)' : 'none',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 7 }}>
                  <span style={{ fontFamily: MONO, fontSize: 9, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', borderRadius: 2, background: 'var(--accent-soft)', color: 'var(--ink-2)', whiteSpace: 'nowrap' }}>{b.theme}</span>
                  <span style={{ fontFamily: MONO, fontSize: 8, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', borderRadius: 2, background: b.ubg, color: b.uc, whiteSpace: 'nowrap' }}>{b.urgency}</span>
                </div>
                <div style={{ fontSize: 10.5, color: 'var(--ink-2)', fontStyle: 'italic', lineHeight: 1.4, marginBottom: 8 }}>{b.example}</div>
                <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', marginBottom: 2 }}>SUGGESTED ANGLE</div>
                <div style={{ fontSize: 11, color: 'var(--ink)', lineHeight: 1.45, marginBottom: 8 }}>{b.angle}</div>
                <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', marginBottom: 2 }}>TARGET PERSONA</div>
                <div style={{ fontSize: 10.5, color: 'var(--ink-2)', marginBottom: 10 }}>{b.persona}</div>
                <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', borderRadius: 2, background: 'var(--gold-soft)', color: 'var(--gold)', whiteSpace: 'nowrap' }}>→ stubbed to Content pipeline</span>
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '9px 16px', borderTop: '1px solid var(--line-2)' }}>
            <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>BRIDGE HIT-RATE</span>
            <div style={{ flex: 1, height: 7, background: 'var(--card-2)', position: 'relative', overflow: 'hidden' }}>
              <div style={{ position: 'absolute', inset: 0, width: '68%', background: 'var(--gold)' }} />
            </div>
            <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 600, color: 'var(--ink)' }}>68%</span>
            <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)' }}>of stubbed briefs reached publish</span>
          </div>
        </div>

        {/* VOICE OF FAMILIES */}
        <div style={{ display: 'grid', gridTemplateColumns: '1.55fr 1fr', gap: 14, marginBottom: 14 }}>
          {/* qualitative feed */}
          <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
              <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Voice of families</div>
              <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>notable verbatims · this week</span>
            </div>
            {VOICE_QUOTES.map((q) => (
              <div key={q.who} style={{ display: 'flex', gap: 11, padding: '11px 16px', borderBottom: '1px solid var(--line)' }}>
                <span aria-hidden style={{ width: 8, height: 8, borderRadius: '50%', background: toneColor(q.tone), flexShrink: 0, marginTop: 4 }} />
                <div>
                  <div style={{ fontSize: 11.5, color: 'var(--ink)', lineHeight: 1.45 }}>{q.quote}</div>
                  <div style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)', marginTop: 4 }}>⌖ {q.who}</div>
                </div>
              </div>
            ))}
          </div>

          {/* quote of the week + sentiment trend */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div style={{ border: '1px solid var(--gold)', background: 'var(--gold-soft)', padding: 15 }}>
              <div style={{ fontFamily: MONO, fontSize: 9, letterSpacing: '.4px', color: 'var(--gold)', fontWeight: 600, marginBottom: 8 }}>★ QUOTE OF THE WEEK</div>
              <div style={{ fontFamily: ARCHIVO, fontSize: 14, fontWeight: 600, color: 'var(--ink)', lineHeight: 1.5 }}>&ldquo;{QUOTE_OF_WEEK.quote}&rdquo;</div>
              <div style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-2)', marginTop: 9 }}>⌖ {QUOTE_OF_WEEK.who}</div>
            </div>

            <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)', padding: 14, flex: 1 }}>
              <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 12.5, color: 'var(--ink)' }}>Sentiment trend</div>
              <div style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)', marginBottom: 11 }}>positive / neutral / negative · share of verbatims</div>
              {SENTIMENT_TREND.map((w) => (
                <div key={w.week} style={{ marginBottom: 9 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                    <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-2)' }}>{w.week}</span>
                    <span style={{ fontFamily: MONO, fontSize: 9, color: 'var(--ink-3)' }}>{w.pos}% pos</span>
                  </div>
                  <div style={{ display: 'flex', height: 9, overflow: 'hidden' }}>
                    <div style={{ width: `${w.pos}%`, background: 'var(--ok)' }} />
                    <div style={{ width: `${w.neu}%`, background: 'var(--ink-3)' }} />
                    <div style={{ width: `${w.neg}%`, background: 'var(--signal)' }} />
                  </div>
                </div>
              ))}
              <div style={{ display: 'flex', gap: 10, marginTop: 8, flexWrap: 'wrap' }}>
                <Legend color="var(--ok)" label="positive" />
                <Legend color="var(--ink-3)" label="neutral" />
                <Legend color="var(--signal)" label="negative" />
              </div>
            </div>
          </div>
        </div>

        {/* FEEDBACK → MARKETING LOOP */}
        <div style={{ border: '1px solid var(--line-2)', background: 'var(--card)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderBottom: '2px solid var(--ink)' }}>
            <div style={{ fontFamily: ARCHIVO, fontWeight: 700, fontSize: 13, color: 'var(--ink)' }}>Feedback → marketing loop</div>
            <span style={{ fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>&quot;marketing needs to know X&quot; · {closureRate}% actioned within 7 days</span>
          </div>
          {FEEDBACK.map((f) => (
            <div key={f.cat} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px', borderBottom: '1px solid var(--line)' }}>
              <span style={{ fontFamily: MONO, fontSize: 8.5, fontWeight: 600, letterSpacing: '.4px', padding: '3px 8px', borderRadius: 2, background: f.bg, color: f.c, minWidth: 124, textAlign: 'center' }}>{f.cat}</span>
              <span style={{ flex: 1, fontSize: 11.5, color: 'var(--ink-2)', lineHeight: 1.4 }}>{f.note}</span>
              <span
                style={{
                  fontFamily: MONO,
                  fontSize: 9,
                  fontWeight: 600,
                  letterSpacing: '.4px',
                  padding: '3px 9px',
                  borderRadius: 2,
                  background: f.actioned ? 'var(--ok-soft)' : 'var(--accent-soft)',
                  color: f.actioned ? 'var(--ok)' : 'var(--ink-3)',
                  minWidth: 188,
                  textAlign: 'center',
                }}
              >
                {f.actioned ? '✓ ' : '○ '}{f.status}
              </span>
            </div>
          ))}
          <div style={{ padding: '9px 16px', fontFamily: MONO, fontSize: 8, color: 'var(--ink-3)' }}>
            CROSS-LINK · top objections → Content briefs (Module 3) · hot families → Nurture + Decision Queue (Module 11)
          </div>
        </div>
      </section>
    </>
  );
}

// ---- Helpers ---------------------------------------------------------------
function toneColor(tone: VoiceQuote['tone']): string {
  if (tone === 'pos') return 'var(--ok)';
  if (tone === 'neg') return 'var(--signal)';
  return 'var(--ink-3)';
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
      <span style={{ width: 9, height: 9, background: color }} />
      <span style={{ fontFamily: MONO, fontSize: 8.5, color: 'var(--ink-3)' }}>{label}</span>
    </span>
  );
}
