import { useMemo, useState } from 'react';
import { FileText, Plus, Search, Upload } from 'lucide-react';
import { Button, Card, Chip } from '../ui';

// Module 12 — Resource Library (GT Marketing Hub spec §3, Module 12). A flat,
// tag-filterable reference shelf: "Simple, useful — no automation." The spec names
// the starter docs to mock as sample uploads (persona dossier, marketing plan,
// brand strategy, outcomes tracker, Brainlifts); we seed those alongside the real
// GT build artifacts so the shelf has honest, non-lorem content to search + filter.
// v1 is intentionally a stub (the brief blesses this module as "a reasonable place
// to stub"): seeded rows + client-side search/filter + an in-session upload. No
// backend table yet — persistence is the documented v2 step.

// The spec's tag vocabulary (Module 12: "filter by type") and file-type badges.
const TYPE_TAGS = [
  'strategy',
  'data',
  'creative',
  'persona',
  'playbook',
] as const;
type TypeTag = (typeof TYPE_TAGS)[number];

type FileKind = 'DOC' | 'SHEET' | 'SLIDES' | 'PDF' | 'MD' | 'HTML';

interface Resource {
  id: string;
  title: string;
  tag: TypeTag;
  kind: FileKind;
  owner: string;
  date: string; // ISO yyyy-mm-dd
  note: string;
}

// Seeded shelf — the spec's named starter docs (top) + the real artifacts this
// build already produced (mocked as sample uploads, per the spec's instruction).
const SEED: readonly Resource[] = [
  { id: 'r-plan', title: 'Go-Forward Marketing Plan', tag: 'strategy', kind: 'DOC', owner: 'Marketing Lead', date: '2026-06-10', note: 'The June to August growth plan: grassroots, content, nurture, conversion push.' },
  { id: 'r-prios', title: 'Suggested Prios', tag: 'strategy', kind: 'SLIDES', owner: 'Marketing Lead', date: '2026-06-12', note: 'Sequencing deck: which workstreams go deep, which get stubbed, and why.' },
  { id: 'r-brand', title: 'Brand Strategy: Analog Futurism', tag: 'creative', kind: 'DOC', owner: 'Content Owner', date: '2026-06-05', note: 'Voice, palette, and the Analog Futurism identity. Marketing craft attributed to Tom Babb.' },
  { id: 'r-outcomes', title: 'Outcomes / Results Tracker', tag: 'data', kind: 'SHEET', owner: 'Budget Owner', date: '2026-06-20', note: 'Weekly KPI actuals vs. targets feeding the canonical scorecard.' },
  { id: 'r-persona', title: 'Persona Dossier v2', tag: 'persona', kind: 'PDF', owner: 'Marketing Lead', date: '2026-05-28', note: 'Gifted-family personas: income, geo, grade, conviction tells. Aggregate, COPPA-safe.' },
  { id: 'r-spec', title: 'GT Marketing Hub: Product Specification v2', tag: 'strategy', kind: 'PDF', owner: 'GT School Marketing', date: '2026-06-26', note: 'The 13-module requirement: owners, data sources, roles, cross-module rules.' },
  { id: 'r-arch', title: 'Brainlift: Growth Cockpit Architecture', tag: 'playbook', kind: 'MD', owner: 'Engineering', date: '2026-06-22', note: 'Data model, adapters, params home, the proposal to approval write path (INV-2).' },
  { id: 'r-threat', title: 'Threat Model & RLS Doctrine', tag: 'playbook', kind: 'MD', owner: 'Engineering', date: '2026-06-18', note: 'Deny-by-default RLS, program isolation, the IDOR that was disclosed and closed (INV-5).' },
  { id: 'r-content', title: 'Content Spec & Brand-Voice Rules', tag: 'creative', kind: 'MD', owner: 'Content Owner', date: '2026-06-15', note: 'The V-1 to V-4 grounding and safety gate the brand-voice auditor suggests against.' },
];

// File-kind badge tone — keeps the mono badges legible without inventing colors.
function kindTone(kind: FileKind): 'neutral' | 'flow' | 'gate' | 'signal' {
  if (kind === 'SHEET') return 'flow';
  if (kind === 'SLIDES' || kind === 'PDF') return 'gate';
  if (kind === 'HTML') return 'signal';
  return 'neutral';
}

const TAG_LABEL: Record<TypeTag, string> = {
  strategy: 'Strategy',
  data: 'Data',
  creative: 'Creative',
  persona: 'Persona',
  playbook: 'Playbook',
};

export default function ResourceLibraryWorkspace(): JSX.Element {
  const [items, setItems] = useState<readonly Resource[]>(SEED);
  const [query, setQuery] = useState('');
  const [activeTags, setActiveTags] = useState<ReadonlySet<TypeTag>>(new Set());
  const [adding, setAdding] = useState(false);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items.filter((r) => {
      const tagOk = activeTags.size === 0 || activeTags.has(r.tag);
      const qOk =
        q === '' ||
        r.title.toLowerCase().includes(q) ||
        r.note.toLowerCase().includes(q) ||
        r.owner.toLowerCase().includes(q);
      return tagOk && qOk;
    });
  }, [items, query, activeTags]);

  function toggleTag(tag: TypeTag): void {
    setActiveTags((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
  }

  function onAdd(r: Resource): void {
    setItems((prev) => [r, ...prev]);
    setAdding(false);
  }

  return (
    <section
      aria-label="Resource Library workspace"
      data-testid="resource-library-workspace"
      style={{ display: 'grid', gap: 'var(--s-5)', maxWidth: 920 }}
    >
      {/* Header — module title + honest count. No gradient hero (banned). */}
      <header style={{ display: 'grid', gap: 'var(--s-2)' }}>
        <h1 style={{ margin: 0 }}>Resource Library</h1>
        <p style={{ margin: 0, color: 'var(--ink-soft)', maxWidth: 64 + 'ch' }}>
          A flat reference shelf: strategy, data, creative, persona, and playbook
          docs. Search and tag-filter; upload adds a row. No automation, no
          versioning: a clean organized shelf.
        </p>
      </header>

      {/* Toolbar — search + tag filters + add. */}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 'var(--s-3)',
          alignItems: 'center',
        }}
      >
        <label
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--s-2)',
            flex: '1 1 240px',
            background: 'var(--surface-2)',
            border: '1px solid var(--line)',
            borderRadius: 8,
            padding: '0 var(--s-3)',
          }}
        >
          <Search size={15} aria-hidden style={{ color: 'var(--ink-soft)' }} />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search title, owner, note…"
            aria-label="Search resources"
            data-testid="resource-search"
            style={{
              border: 'none',
              outline: 'none',
              background: 'transparent',
              padding: 'var(--s-2) 0',
              width: '100%',
              font: 'inherit',
              color: 'var(--ink)',
            }}
          />
        </label>

        <div style={{ display: 'flex', gap: 'var(--s-2)', flexWrap: 'wrap' }}>
          {TYPE_TAGS.map((tag) => {
            const on = activeTags.has(tag);
            return (
              <button
                key={tag}
                type="button"
                onClick={() => toggleTag(tag)}
                aria-pressed={on}
                data-testid={`resource-tag-${tag}`}
                style={{
                  cursor: 'pointer',
                  border: `1px solid ${on ? 'var(--ink)' : 'var(--line)'}`,
                  background: on ? 'var(--ink)' : 'transparent',
                  color: on ? 'var(--on-ink)' : 'var(--ink-soft)',
                  borderRadius: 999,
                  padding: '4px 12px',
                  font: 'inherit',
                  fontSize: 13,
                }}
              >
                {TAG_LABEL[tag]}
              </button>
            );
          })}
        </div>

        <Button
          variant="primary"
          icon={Plus}
          onClick={() => setAdding((v) => !v)}
          data-testid="resource-add-toggle"
        >
          {adding ? 'Close' : 'Add resource'}
        </Button>
      </div>

      {/* Inline upload (progressive, not a modal — modals are banned by default). */}
      {adding && <UploadForm onAdd={onAdd} />}

      {/* The shelf. */}
      <Card>
        {filtered.length === 0 ? (
          <p
            style={{ color: 'var(--ink-soft)', margin: 'var(--s-3) 0' }}
            data-testid="resource-empty"
          >
            No resources match. Clear the search or a tag filter.
          </p>
        ) : (
          <ul
            style={{ listStyle: 'none', margin: 0, padding: 0 }}
            data-testid="resource-list"
          >
            {filtered.map((r, i) => (
              <li
                key={r.id}
                style={{
                  display: 'grid',
                  gridTemplateColumns: '54px 1fr auto',
                  gap: 'var(--s-3)',
                  alignItems: 'start',
                  padding: 'var(--s-3) 0',
                  borderTop:
                    i === 0 ? 'none' : '1px solid var(--line-2)',
                }}
              >
                <span
                  className="mono"
                  aria-label={`${r.kind} file`}
                  style={{
                    fontSize: 10,
                    letterSpacing: '0.06em',
                    textAlign: 'center',
                    padding: '3px 0',
                    borderRadius: 5,
                    border: '1px solid var(--line)',
                    color: 'var(--ink-soft)',
                  }}
                >
                  {r.kind}
                </span>

                <div style={{ display: 'grid', gap: 4 }}>
                  <span
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 'var(--s-2)',
                      fontWeight: 600,
                    }}
                  >
                    <FileText
                      size={14}
                      aria-hidden
                      style={{ color: 'var(--ink-soft)', flexShrink: 0 }}
                    />
                    {r.title}
                  </span>
                  <span
                    style={{ color: 'var(--ink-soft)', fontSize: 13 }}
                  >
                    {r.note}
                  </span>
                  <span
                    className="mono"
                    style={{ color: 'var(--ink-soft)', fontSize: 11 }}
                  >
                    {r.owner} · {r.date}
                  </span>
                </div>

                <Chip tone={kindTone(r.kind) === 'neutral' ? 'neutral' : kindTone(r.kind)}>
                  {TAG_LABEL[r.tag]}
                </Chip>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <p
        className="mono"
        style={{ color: 'var(--ink-soft)', fontSize: 11, margin: 0 }}
      >
        {filtered.length} of {items.length} resources · Module 12
      </p>
    </section>
  );
}

// The inline upload form — adds a row to the in-session shelf. v1 persistence is a
// documented stub; the row appears immediately with its assigned tags (spec:
// "Upload confirmation — new resources appear immediately with assigned tags").
function UploadForm({ onAdd }: { onAdd: (r: Resource) => void }): JSX.Element {
  const [title, setTitle] = useState('');
  const [tag, setTag] = useState<TypeTag>('strategy');
  const [kind, setKind] = useState<FileKind>('DOC');
  const [owner, setOwner] = useState('');

  const canSubmit = title.trim() !== '' && owner.trim() !== '';
  const today = '2026-06-27';

  function submit(): void {
    if (!canSubmit) return;
    onAdd({
      id: `r-${title.trim().toLowerCase().replace(/\s+/g, '-')}-${kind}`,
      title: title.trim(),
      tag,
      kind,
      owner: owner.trim(),
      date: today,
      note: 'Uploaded this session.',
    });
    setTitle('');
    setOwner('');
  }

  const inputStyle = {
    border: '1px solid var(--line)',
    borderRadius: 8,
    background: 'var(--surface-2)',
    padding: 'var(--s-2) var(--s-3)',
    font: 'inherit',
    color: 'var(--ink)',
  } as const;

  return (
    <Card>
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 'var(--s-3)',
          alignItems: 'end',
        }}
        data-testid="resource-upload-form"
      >
        <label style={{ display: 'grid', gap: 4, flex: '2 1 220px' }}>
          <span className="mono" style={{ fontSize: 11, color: 'var(--ink-soft)' }}>
            Title
          </span>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Resource title"
            data-testid="resource-upload-title"
            style={inputStyle}
          />
        </label>

        <label style={{ display: 'grid', gap: 4, flex: '1 1 140px' }}>
          <span className="mono" style={{ fontSize: 11, color: 'var(--ink-soft)' }}>
            Owner
          </span>
          <input
            value={owner}
            onChange={(e) => setOwner(e.target.value)}
            placeholder="Owner"
            data-testid="resource-upload-owner"
            style={inputStyle}
          />
        </label>

        <label style={{ display: 'grid', gap: 4, flex: '1 1 120px' }}>
          <span className="mono" style={{ fontSize: 11, color: 'var(--ink-soft)' }}>
            Type
          </span>
          <select
            value={tag}
            onChange={(e) => setTag(e.target.value as TypeTag)}
            data-testid="resource-upload-tag"
            style={inputStyle}
          >
            {TYPE_TAGS.map((t) => (
              <option key={t} value={t}>
                {TAG_LABEL[t]}
              </option>
            ))}
          </select>
        </label>

        <label style={{ display: 'grid', gap: 4, flex: '1 1 110px' }}>
          <span className="mono" style={{ fontSize: 11, color: 'var(--ink-soft)' }}>
            File
          </span>
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as FileKind)}
            data-testid="resource-upload-kind"
            style={inputStyle}
          >
            {(['DOC', 'SHEET', 'SLIDES', 'PDF', 'MD', 'HTML'] as const).map(
              (k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ),
            )}
          </select>
        </label>

        <Button
          variant="flow"
          icon={Upload}
          onClick={submit}
          disabled={!canSubmit}
          data-testid="resource-upload-submit"
        >
          Upload
        </Button>
      </div>
    </Card>
  );
}
