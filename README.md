# GT Growth Cockpit

Deterministic core, LLM at the edge, eval-gated. All data is **synthetic** and shaped like GT's real
schema — there is no production PII in this repo (CLAUDE.md INV-1; THREAT_MODEL.md §5).

The planning doc suite is the source of truth: `PROJECT.md` (scope), `ARCHITECTURE.md`,
`TECH_STACK.md`, `THREAT_MODEL.md`, `CONTENT_SPEC.md`, and `CLAUDE.md` (build rules).

## Setup

```bash
# 1. Activate the enforced quality gate (CLAUDE.md §5, §8). Run once per clone.
#    Wires the committed pre-push + commit-msg hooks. Never bypass with --no-verify (INV-12).
git config core.hooksPath .githooks

# 2. Backend (Python 3.12, managed by uv).
cd backend && uv sync
cd ..

# 3. Tunables — every magic number lives in params/params.yaml (INV-11).
cp params/params.example.yaml params/params.yaml

# 4. Env vars — registry is TECH_STACK.md §5. Placeholders only; no real secrets.
cp .env.example .env            # backend env (edit locally)

# 5. Front end (React + Vite, Node 22).
cd frontend && cp .env.example .env && npm install
cd ..
```

### Run

```bash
cd backend && uv run uvicorn app.main:app --reload   # API at http://localhost:8000
cd frontend && npm run dev                            # UI dev server (Vite)
```

## Quality gate

The committed git hooks (`.githooks/`) are the gate; CI runs the **same** checks in the same order
(`.github/workflows/ci.yml`). Activated by `git config core.hooksPath .githooks` (step 1 above).

| # | Check | Command |
|---|---|---|
| 1 | PII / secret scan (THREAT_MODEL.md §5) | `python scripts/pii_scan.py` |
| 2 | Runtime dep budget ≤ 15 (TECH_STACK.md §4.1) | `python scripts/check_dep_budget.py` |
| 3 | Lint | `cd backend && uv run ruff check .` |
| 4 | Format | `cd backend && uv run ruff format --check .` |
| 5 | Types (strict) | `cd backend && uv run mypy app` |
| 6 | Tests | `cd backend && uv run pytest -q` |

- **pre-push hook** (`.githooks/pre-push`) runs all six, failing fast.
- **commit-msg hook** (`.githooks/commit-msg`) enforces Conventional Commits:
  `type(scope): subject` — types `feat|fix|docs|chore|test|refactor|ci|perf|build`.
- **PII scan self-test:** `python scripts/pii_scan.py --self-test` plants a fixture and proves the
  gate flags the C-SYN-2 cluster, SSNs, lat/long, and secret-shaped strings.
