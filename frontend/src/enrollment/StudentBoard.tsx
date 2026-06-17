import { useEffect, useState } from 'react';
import { Users } from 'lucide-react';
import { apiFetch } from '../config';
import { Card, Chip } from '../ui';
import { fmtUSD } from './format';

// Per-child board (A-24). Fetches GET /students — households the server has
// already ranked (each household by its most-recoverable child, students within
// a household by recoverable_now desc) — and renders them IN THE ORDER RECEIVED
// (the server owns the ranking; this UI never re-sorts). Each child runs its own
// funnel (one application per child), so every ROW is a STUDENT with a distinct
// label ("Rivera household — Alex · Grade 3"), grouped under its household; the
// household header shows its $-at-risk = one tuition per still-active child.
// Native fetch only (≤12-dep budget). Read-only (INV-2).

interface StudentRow {
  student_id: string;
  family_id: string;
  household_name: string;
  display_label: string;
  synthetic_first_name: string;
  grade: string;
  current_stage: string;
  funding_type?: string | null;
  funding_state: string;
  stall_reason?: string | null;
  score: number;
  recoverability: number;
  value: number;
  recoverable_now: number;
  freshness: number;
  recovery_state: string;
}

interface HouseholdGroup {
  family_id: string;
  household_name: string;
  value_at_risk: number;
  students: StudentRow[];
}

interface StudentBoardResponse {
  households: HouseholdGroup[];
  total_students: number;
  total_value_at_risk: number;
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: StudentBoardResponse };

interface StudentBoardProps {
  selectedFamilyId?: string;
  onSelectFamily?: (familyId: string) => void;
}

// A child's funding tier rendered as the operator-facing label, not the raw enum
// (A-23/A-24: every targeted household is full-pay — Texas voucher or self-pay).
function fundingLabel(fundingType: string | null | undefined): string | null {
  if (fundingType == null) return null;
  if (fundingType === 'self_pay') return 'Self-pay';
  if (fundingType.startsWith('tefa')) return 'Texas voucher';
  return fundingType;
}

// The board scope, mirroring the server's GET /students?scope axis. ``active``
// (default) is the live recovery slice — closed-out children don't lead the
// board; history/all surface recovered + dismissed children too.
type Scope = 'active' | 'history' | 'all';

const SCOPES: ReadonlyArray<{ key: Scope; label: string }> = [
  { key: 'active', label: 'Active' },
  { key: 'history', label: 'History' },
  { key: 'all', label: 'All' },
];

export default function StudentBoard({
  selectedFamilyId,
  onSelectFamily,
}: StudentBoardProps = {}): JSX.Element {
  const [scope, setScope] = useState<Scope>('active');
  const [state, setState] = useState<LoadState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    apiFetch(`/students?scope=${scope}`)
      .then((res) => {
        if (!res.ok) throw new Error(`students request failed: ${res.status}`);
        return res.json() as Promise<StudentBoardResponse>;
      })
      .then((data) => {
        if (!cancelled) setState({ status: 'ready', data });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : 'unknown error';
          setState({ status: 'error', message });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [scope]);

  const scopeToggle = (
    <div
      role="tablist"
      aria-label="Board scope"
      data-testid="student-scope-toggle"
      style={{ display: 'inline-flex', gap: '2px' }}
    >
      {SCOPES.map(({ key, label }) => {
        const active = key === scope;
        return (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={active}
            data-testid={`student-scope-${key}`}
            onClick={() => setScope(key)}
            style={{
              border: '1px solid var(--line)',
              background: active ? 'var(--ink)' : 'var(--surface)',
              color: active ? 'var(--surface)' : 'var(--ink)',
              borderRadius: 'var(--r-pill)',
              padding: '3px 10px',
              fontSize: 11,
              fontWeight: 600,
              cursor: 'pointer',
              fontFamily: 'inherit',
            }}
          >
            {label}
          </button>
        );
      })}
    </div>
  );

  // The header bar (title + scope toggle) renders in EVERY state so the operator
  // can switch scope while loading or after an error — not just once data lands.
  const headerBar = (
    <div
      className="lab"
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 'var(--s-2)',
        marginBottom: 'var(--s-2)',
      }}
    >
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 'var(--s-1)',
        }}
      >
        <Users size={11} aria-hidden /> Students — one application per child,
        grouped by household
      </span>
      {scopeToggle}
    </div>
  );

  if (state.status === 'loading') {
    return (
      <section aria-label="Student board" data-testid="student-board">
        {headerBar}
        <p data-testid="student-board-loading" className="lab">
          Loading students…
        </p>
      </section>
    );
  }
  if (state.status === 'error') {
    return (
      <section aria-label="Student board" data-testid="student-board">
        {headerBar}
        <p
          data-testid="student-board-error"
          role="alert"
          style={{ color: 'var(--signal-ink)', fontSize: 'var(--fs-sm)' }}
        >
          Could not load students: {state.message}
        </p>
      </section>
    );
  }

  const { households, total_students, total_value_at_risk } = state.data;
  const selectable = onSelectFamily !== undefined;

  return (
    <section aria-label="Student board" data-testid="student-board">
      {headerBar}
      <div
        className="lab"
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'flex-end',
          gap: 'var(--s-2)',
          marginBottom: 'var(--s-2)',
        }}
      >
        <span
          data-testid="student-board-total"
          style={{ color: 'var(--muted)' }}
        >
          {total_students} students · {fmtUSD(total_value_at_risk)} at risk
        </span>
      </div>

      {households.map((household) => (
        <div
          key={household.family_id}
          data-testid="household-group"
          style={{ marginBottom: 'var(--s-2)' }}
        >
          <Card pad={false}>
            <div
              className="household-head"
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 'var(--s-2)',
                padding: 'var(--s-2) var(--s-4)',
                borderBottom: '1px solid var(--line)',
                background: 'var(--flow-wash)',
              }}
            >
              <span style={{ fontSize: 'var(--fs-body)', fontWeight: 600 }}>
                {household.household_name}
              </span>
              <span
                className="mono"
                data-testid="household-value-at-risk"
                title="One tuition per still-active child"
                style={{
                  fontSize: 'var(--fs-sm)',
                  color: 'var(--gate)',
                  fontWeight: 600,
                }}
              >
                {fmtUSD(household.value_at_risk)} · {household.students.length}{' '}
                {household.students.length === 1 ? 'child' : 'children'}
              </span>
            </div>
            <ol
              className="student-list"
              style={{ listStyle: 'none', margin: 0, padding: 0 }}
            >
              {household.students.map((student, i) => {
                const isActive =
                  selectable && student.family_id === selectedFamilyId;
                const innerStyle = {
                  display: 'flex',
                  alignItems: 'center',
                  gap: 'var(--s-3)',
                  padding: 'var(--s-3) var(--s-4)',
                  width: '100%',
                  textAlign: 'left' as const,
                  font: 'inherit',
                  color: 'inherit',
                  cursor: selectable ? 'pointer' : 'default',
                  background: isActive ? 'var(--flow-wash)' : 'transparent',
                  border: 'none',
                  borderLeft: isActive
                    ? '3px solid var(--ink)'
                    : '3px solid transparent',
                };
                const funding = fundingLabel(student.funding_type);
                const body = (
                  <>
                    <span
                      className="row-name"
                      style={{
                        flex: 1,
                        fontSize: 'var(--fs-body)',
                        fontWeight: 500,
                        minWidth: 0,
                      }}
                    >
                      {student.display_label}
                    </span>
                    <span className="row-stage">
                      <Chip>{student.current_stage}</Chip>
                    </span>
                    <span
                      className="row-recovery lab"
                      data-testid="student-recovery-state"
                      style={{
                        color: 'var(--muted)',
                        minWidth: 64,
                        textAlign: 'right',
                      }}
                    >
                      {student.recovery_state}
                    </span>
                    {funding != null && (
                      <span
                        className="row-funding lab"
                        style={{
                          color: 'var(--muted)',
                          minWidth: 84,
                          textAlign: 'right',
                        }}
                      >
                        {funding}
                      </span>
                    )}
                    <span
                      className="row-recoverability mono"
                      data-testid="student-recoverability"
                      title="Recoverability (likelihood)"
                      style={{
                        fontSize: 'var(--fs-sm)',
                        color: 'var(--flow)',
                        minWidth: 48,
                        textAlign: 'right',
                      }}
                    >
                      {student.recoverability.toFixed(2)}
                    </span>
                  </>
                );
                return (
                  <li
                    key={student.student_id}
                    className={`student-row${isActive ? ' active' : ''}`}
                    data-testid="student-row"
                    aria-current={isActive ? 'true' : undefined}
                    style={{ borderTop: i ? '1px solid var(--line)' : 'none' }}
                  >
                    {selectable ? (
                      <button
                        type="button"
                        data-testid={`student-row-${student.student_id}`}
                        onClick={() => onSelectFamily(student.family_id)}
                        style={innerStyle}
                      >
                        {body}
                      </button>
                    ) : (
                      <div
                        data-testid={`student-row-${student.student_id}`}
                        style={innerStyle}
                      >
                        {body}
                      </div>
                    )}
                  </li>
                );
              })}
            </ol>
          </Card>
        </div>
      ))}
    </section>
  );
}
