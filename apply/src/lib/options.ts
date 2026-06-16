// Canonical option sets for the mock apply form.
//
// These are the EXACT token sets the cockpit recognizes (from the backend's
// models.py StrEnums + data/synthetic.py + A-24/W3 brief). The form exposes ONLY
// these as dropdowns/checkboxes/radios — there is no free-typed input anywhere,
// which is how INV-1 (no PII) / INV-6 (no child keys) hold by FORM SHAPE rather
// than by scrubbing. A value the user "types" cannot exist because no text input
// is rendered (see steps/*).

// leads_new.product_interest — the Postgres `product_interest` enum.
export const PRODUCT_INTEREST = ['campus', 'anywhere', 'summer_camp'] as const;
export type ProductInterest = (typeof PRODUCT_INTEREST)[number];

export const PRODUCT_INTEREST_LABEL: Record<ProductInterest, string> = {
  campus: 'GT Campus (in-person)',
  anywhere: 'GT Anywhere (online)',
  summer_camp: 'Summer Camp',
};

// leads_new.source AND family_record.attribution_source. The real form had more
// options; this is the canonical set the cockpit recognizes (W3 brief).
export const ATTRIBUTION_SOURCE = [
  'organic_search',
  'branded_search',
  'referral',
  'paid_social',
  'newsletter',
  'webinar',
  'partner',
  'direct',
] as const;
export type AttributionSource = (typeof ATTRIBUTION_SOURCE)[number];

export const ATTRIBUTION_SOURCE_LABEL: Record<AttributionSource, string> = {
  organic_search: 'Found you on Google',
  branded_search: 'Searched for "GT School"',
  referral: 'A friend referred us',
  paid_social: 'An ad on social media',
  newsletter: 'Your newsletter',
  webinar: 'A webinar / info session',
  partner: 'A partner organization',
  direct: 'Came here directly',
};

// leads_new.region — aggregate region label only (INV-6 / P-4: no precise geo).
export const REGION = [
  'Northeast',
  'Southeast',
  'Midwest',
  'Southwest',
  'Mountain West',
  'Pacific Northwest',
  'West Coast',
  'Mid-Atlantic',
  'Great Plains',
] as const;
export type Region = (typeof REGION)[number];

// leads_new.grade_interest — grade BAND only (INV-1/INV-6: never a real DOB).
export const GRADE_INTEREST = [
  'K',
  '1',
  '2',
  '3',
  '4',
  '5',
  '6',
  '7',
  '8',
] as const;
export type GradeInterest = (typeof GRADE_INTEREST)[number];

// num_children — small-integer dropdown (the Interest form's "how many children").
export const NUM_CHILDREN = [1, 2, 3, 4] as const;
export type NumChildren = (typeof NUM_CHILDREN)[number];

// family_record.funding_type — the targeted full-pay tiers (A-23: GT only targets
// full GT-Anywhere tuition via Texas voucher or self-pay). Informational on the
// Tuition step; affects funding_type if set.
export const FUNDING_TYPE = ['tefa_standard', 'self_pay'] as const;
export type FundingType = (typeof FUNDING_TYPE)[number];

export const FUNDING_TYPE_LABEL: Record<FundingType, string> = {
  tefa_standard: 'Texas voucher (TEFA)',
  self_pay: 'Self-pay',
};
