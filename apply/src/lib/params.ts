// Client-side params for the apply SPA (R3).
//
// INV-11 (no magic numbers): every tunable has exactly ONE canonical home. The
// canonical home for these values is the repo-root `params/params.yaml`; the
// browser bundle cannot read that YAML at runtime (it is a server/backend file),
// so this module MIRRORS the canonical values and cites the source line. When a
// value changes in params.yaml, change it HERE too — `deriveNextStep` reads it
// from here, never from an inlined literal. Deadlines/windows below are NOT
// invented numbers: they are the committed `params.yaml` tunables.
//
// (If/when the SPA gains a build-time YAML import or a generated config, this file
// becomes the generated artifact — the contract stays "one canonical home".)

export interface ApplyParams {
  /**
   * Days from "now" within which an awarded-but-unconfirmed voucher counts as
   * "near deadline" → the `byWhen` horizon for the reconfirm next-step.
   * Canonical: params.yaml `work_queue.deadline_horizon_days` (= 14).
   */
  deadlineHorizonDays: number;
  /**
   * Days a family may sit stalled before it is flagged. Used as the soft
   * "respond by" horizon for application/enrollment next-steps.
   * Canonical: params.yaml `work_queue.stall_window_days` (= 14).
   */
  stallWindowDays: number;
}

// Mirrors params.yaml (work_queue: deadline_horizon_days: 14, stall_window_days: 14).
export const APPLY_PARAMS: ApplyParams = {
  deadlineHorizonDays: 14,
  stallWindowDays: 14,
};
