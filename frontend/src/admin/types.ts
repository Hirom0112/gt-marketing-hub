// Shared admin-dashboard types (admin-dashboard redesign). One canonical home for
// the read shapes the tabs + the detail panel consume, so the components never
// re-declare them. All read-only projections off the existing API (INV-2).

// One ranked work-queue row (GET /work-queue). We read only the fields the admin
// surfaces need; the queue carries more (recoverability/score/etc.) which we ignore.
export interface WorkQueueRow {
  family_id: string;
  display_name: string;
  value: number;
  contact_status: string;
  recovery_state: string;
  current_stage: string;
  assigned_rep_id: string | null;
  stall_date: string;
  num_children: number;
  funding_type: string | null;
  recoverable_now: number;
}

// One family summary row (GET /families → FamilyRecord[]). Only the identity
// fields the Students search needs (the full record carries far more).
export interface FamilySummary {
  family_id: string;
  display_name: string;
}

// The Leads-tab calendar payload (GET /enrollment/leads-calendar).
export interface LeadsCalendarAgent {
  agent_id: string;
  synthetic_name: string;
  count: number;
}
export interface LeadsCalendarDay {
  day: number;
  agents: LeadsCalendarAgent[];
  unowned_count: number;
  total: number;
}
export interface LeadsCalendarResponse {
  month: string;
  days: LeadsCalendarDay[];
}

// A selected reconcile issue — either a HubSpot-vs-dashboard seam divergence or a
// SIS paid-not-in-SIS row — fed to the right panel when no family is selected.
export interface ReconcileIssue {
  kind: 'seam' | 'sis';
  family_id: string;
  // The human label for the discrepancy (e.g. "unsynced" / "Paid · not in SIS").
  status: string;
  // The seam status (seam issues only) so the panel can offer push/flag.
  seam_status?: string;
}
