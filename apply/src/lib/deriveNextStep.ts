// R3 — the pure, params-driven "next step + by when" deriver (NO LLM, INV-2).
//
// This re-homes the dead backend `derive_stall_reason` rule table
// (stage_machine.py:94) as the FAMILY-facing answer to "what do I do next, and by
// when?". It is a pure function of the family's derived stage, its voucher
// funding_state, its enrollment-form counts, and params — deterministic and
// testable (CLAUDE.md §3 core purity, §4.1 strict TDD).
//
// Deadlines are read from params (INV-11), never invented. The voucher lane is
// FAIL-CLOSED (INV-10): a family is never told "all set" before the first
// installment is received — `awarded_selfreport` / `gt_confirmed` still surface a
// "reconfirm" next step, because a self-report or a GT confirmation is NOT yet
// money in hand.

import type { ApplyParams } from './params';

/** The four pipeline stages (mirrors APPLICATION_STAGES in apply.ts). */
export type ApplyStage = 'interest' | 'apply' | 'enroll' | 'tuition';

/**
 * The voucher funding lifecycle (mirrors the DB `funding_state` enum, 0001_init).
 * `none` → `applied` → `awarded_selfreport` → `gt_confirmed` →
 * `first_installment_received` → `funded`. Only the last two are "money in hand".
 */
export type FundingState =
  | 'none'
  | 'applied'
  | 'awarded_selfreport'
  | 'gt_confirmed'
  | 'first_installment_received'
  | 'funded';

export interface FormProgress {
  signed: number;
  total: number;
}

export interface NextStep {
  /** Family-facing copy (inline UI string is fine — not a tunable). */
  label: string;
  /** ISO date (YYYY-MM-DD) the action is due by, or null when nothing is due. */
  byWhen: string | null;
}

/** A voucher is "money in hand" only once an installment is received (INV-10). */
const CONFIRMED_FUNDING: ReadonlySet<FundingState> = new Set<FundingState>([
  'first_installment_received',
  'funded',
]);

function isoDateDaysFrom(now: Date, days: number): string {
  return new Date(now.getTime() + days * 86_400_000).toISOString().slice(0, 10);
}

/**
 * Derive the family's single next step + due date.
 *
 * @param stage         The DERIVED pipeline stage (never the stored placeholder).
 * @param fundingState  The voucher funding_state (fail-closed; see CONFIRMED_FUNDING).
 * @param forms         Enrollment-form signed/total counts.
 * @param params        Tunables (deadline/stall windows) — INV-11, from params.ts.
 * @param now           Reference time (injectable so byWhen is deterministic).
 */
export function deriveNextStep(
  stage: ApplyStage,
  fundingState: FundingState,
  forms: FormProgress,
  params: ApplyParams,
  now: Date = new Date(),
): NextStep {
  switch (stage) {
    case 'interest':
      return {
        label: 'Submit your application',
        byWhen: isoDateDaysFrom(now, params.stallWindowDays),
      };
    case 'apply':
      return {
        label: 'Start your enrollment forms',
        byWhen: isoDateDaysFrom(now, params.stallWindowDays),
      };
    case 'enroll':
      return {
        label: `Finish your enrollment forms (${forms.signed} of ${forms.total} signed)`,
        byWhen: isoDateDaysFrom(now, params.stallWindowDays),
      };
    case 'tuition':
      // Fail-closed voucher lane (INV-10): only an installment-in-hand state is
      // "all set"; everything before it — including a GT confirmation — still
      // needs the family to reconfirm in their state voucher portal.
      if (CONFIRMED_FUNDING.has(fundingState)) {
        return { label: "You're all set — your spot is confirmed", byWhen: null };
      }
      return {
        label: 'Reconfirm your voucher in your state portal',
        byWhen: isoDateDaysFrom(now, params.deadlineHorizonDays),
      };
  }
}
