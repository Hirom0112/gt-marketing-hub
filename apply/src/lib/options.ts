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

// ---------------------------------------------------------------------------
// Structural-only option sets for the rebuilt Apply + Enroll forms (A-24). These
// are NOT persisted to new DB columns — they drive the faithful UX + per-field
// drop-off telemetry only. Every one is a closed dropdown/radio set: no field is
// free-typed, so INV-1 (no PII) / INV-6 (no child keys) hold by FORM SHAPE.
// ---------------------------------------------------------------------------

// A generic yes/no for the eligibility / consent radios + toggles.
export const YES_NO = ['yes', 'no'] as const;
export type YesNo = (typeof YES_NO)[number];
export const YES_NO_LABEL: Record<YesNo, string> = { yes: 'Yes', no: 'No' };

// Parent/Guardian relationship to the child (structural, not a name).
export const RELATIONSHIP = [
  'mother',
  'father',
  'guardian',
  'grandparent',
  'foster_parent',
  'other',
] as const;
export type Relationship = (typeof RELATIONSHIP)[number];
export const RELATIONSHIP_LABEL: Record<Relationship, string> = {
  mother: 'Mother',
  father: 'Father',
  guardian: 'Legal guardian',
  grandparent: 'Grandparent',
  foster_parent: 'Foster parent',
  other: 'Other',
};

// US state — region-band proxy; aggregate only (INV-6 / P-4: no precise geo).
// A coarse list is enough for the structural mirror; not persisted.
export const US_STATE = [
  'Texas',
  'California',
  'New York',
  'Florida',
  'Illinois',
  'Washington',
  'Colorado',
  'Arizona',
  'Georgia',
  'Other',
] as const;
export type UsState = (typeof US_STATE)[number];

// Child gender (structural radio; never keyed to an identity — synthetic child).
export const CHILD_GENDER = ['female', 'male', 'nonbinary', 'prefer_not'] as const;
export type ChildGender = (typeof CHILD_GENDER)[number];
export const CHILD_GENDER_LABEL: Record<ChildGender, string> = {
  female: 'Female',
  male: 'Male',
  nonbinary: 'Non-binary',
  prefer_not: 'Prefer not to say',
};

// Desired enrollment year.
export const ENROLLMENT_YEAR = ['2026', '2027', '2028'] as const;
export type EnrollmentYear = (typeof ENROLLMENT_YEAR)[number];

// Current school situation.
export const SCHOOL_SITUATION = [
  'public_school',
  'private_school',
  'charter_school',
  'homeschool',
  'not_enrolled',
] as const;
export type SchoolSituation = (typeof SCHOOL_SITUATION)[number];
export const SCHOOL_SITUATION_LABEL: Record<SchoolSituation, string> = {
  public_school: 'Currently in public school',
  private_school: 'Currently in private school',
  charter_school: 'Currently in charter school',
  homeschool: 'Currently homeschooled',
  not_enrolled: 'Not currently enrolled',
};

// How the family intends to use GT.
export const GT_USAGE = [
  'full_time',
  'supplemental',
  'after_school',
  'summer_only',
] as const;
export type GtUsage = (typeof GT_USAGE)[number];
export const GT_USAGE_LABEL: Record<GtUsage, string> = {
  full_time: 'Full-time school replacement',
  supplemental: 'Supplemental to current school',
  after_school: 'After-school enrichment',
  summer_only: 'Summer only',
};

// Tuition billing cadence (Enroll → tuition_agreement form). Informational only.
export const BILLING_CADENCE = ['annual', 'semester', 'monthly'] as const;
export type BillingCadence = (typeof BILLING_CADENCE)[number];
export const BILLING_CADENCE_LABEL: Record<BillingCadence, string> = {
  annual: 'Pay annually (one payment)',
  semester: 'Pay by semester (2 payments)',
  monthly: 'Pay monthly (10 payments)',
};

// Consent dropdowns for the data_collection_consent enroll form.
export const CONSENT_CHOICE = ['agree', 'decline'] as const;
export type ConsentChoice = (typeof CONSENT_CHOICE)[number];
export const CONSENT_CHOICE_LABEL: Record<ConsentChoice, string> = {
  agree: 'I agree',
  decline: 'I decline',
};
