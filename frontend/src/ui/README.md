# `ui/` — shared design primitives (S8 Wave 1)

Token-driven, presentational, no `any`, no `fetch`. Import from `@/ui` (`./ui`).
All colour/spacing/type comes from `src/theme.css` `:root` vars — **never write a
raw hex, font stack, or radius in a component**; add a token instead (visual INV-11).

**Tokens** (`theme.css`): neutral base `--ink / --ink-soft / --paper / --surface /
--surface-2 / --line / --line-strong / --muted / --on-ink`; three accents each with
`<accent> / <accent>-strong / <accent>-wash / <accent>-ink` (AA-safe text on the
wash) — `signal` (attention/blockers), `flow` (healthy/synced), `gate` (funding/
placeholder); `--mono` / `--sans`; type scale `--fs-*`; radius `--r-*`; spacing
`--s-*`; `--focus-ring`, `--shadow-*`, `--ease`/`--dur`. Helpers: `.lab` (mono
uppercase micro-label), `.mono`, `.scroll`.

**Primitives** (prop contracts):

- `Card({ children, pad?, className?, style? })` — raised surface, 12px radius, hairline.
- `Chip({ children, tone?, title? })` — mono status chip; `tone: neutral|signal|flow|gate`.
- `Button({ variant?, icon?, ...buttonProps })` — `variant: default|primary|signal`;
  `icon` is a `LucideIcon`; forwards native `<button>` props (`onClick`, `disabled`, …).
- `Field({ label, value })` — labelled read-only value in an inset well.
- `Tab({ label, active, onSelect, icon? })` / `TabBar({ tabs, active, onSelect, ariaLabel? })`
  — pill tabs; `tabs: TabItem<K>[]` where `TabItem = { key, label, icon? }`. Generic over key `K`.
- `Stat({ label, value, note?, tone? })` / `KpiCard(StatProps)` — big mono metric;
  `tone` tints the value (default neutral=ink). `KpiCard` wraps `Stat` in a `Card`.
- `PlaceholderBadge({ label? })` — the INV-9 gold "PLACEHOLDER" marker for simulated surfaces.
- `WorkspaceToggle({ options, active, onSelect, ariaLabel? })` — top-bar segmented control;
  `options: WorkspaceOption<K>[]` where `WorkspaceOption = { key, label, icon }`. Generic over `K`.

Icons come from `lucide-react` (3/12 runtime deps). Pass the icon **component**
(e.g. `icon={Megaphone}`), not an element.
