// The mock apply SPA — mirrors apply.gt.school's flow with faithful STRUCTURE /
// trimmed depth (A-24 / S18).
//
// Surfaces (S18 added the marketing landing, the candidacy modal, and the
// "My Applications" dashboard around the existing 4-step flow):
//
//   landing    → marketing hero ("The MIT of K-8") + Begin Application CTA
//   candidacy  → "Secure Your Candidacy for Fall 2026" modal (prefilled-synthetic
//                identity read-only, household-income dropdown, SMS-consent) →
//                writes leads_new (family visible in cockpit here)
//   apply      → long multi-section app form → app_form (stage `apply`)
//   enroll     → 7-form left-rail sub-stepper → enrollment_forms (stage `enroll`)
//   tuition    → $1,000 deposit → forms_signed=6 + unlock (stage `tuition`)
//   dashboard  → "My Applications" hub: one card per family_record the session
//                created, X/4 progress, delete, "+ Add Another Child"
//
// Telemetry is step → form → field deep with a monotonic nav_seq. Everything is
// dropdown/checkbox/radio/read-only-synthetic — no free-typed PII anywhere
// (INV-1/INV-6). New dropdown selections are NOT persisted to DB columns.

import { useCallback, useEffect, useState, type ReactElement } from 'react';
import {
  createFamily,
  deleteApplication,
  deriveInterestAnswers,
  ensureAnonSession,
  fetchApplications,
  submitApply,
  submitEnroll,
  submitInterest,
  submitTuition,
  type ApplicationSummary,
  type ApplySession,
  type MinimalSupabase,
} from './lib/apply';
import {
  ATTRIBUTION_SOURCE,
  ATTRIBUTION_SOURCE_LABEL,
  BILLING_CADENCE,
  BILLING_CADENCE_LABEL,
  CHILD_GENDER,
  CHILD_GENDER_LABEL,
  CONSENT_CHOICE,
  CONSENT_CHOICE_LABEL,
  ENROLLMENT_YEAR,
  FUNDING_TYPE,
  FUNDING_TYPE_LABEL,
  GRADE_INTEREST,
  GT_USAGE,
  GT_USAGE_LABEL,
  HOUSEHOLD_INCOME,
  HOUSEHOLD_INCOME_LABEL,
  NUM_CHILDREN,
  PRODUCT_INTEREST,
  PRODUCT_INTEREST_LABEL,
  REGION,
  RELATIONSHIP,
  RELATIONSHIP_LABEL,
  SCHOOL_SITUATION,
  SCHOOL_SITUATION_LABEL,
  US_STATE,
  YES_NO,
  YES_NO_LABEL,
  type AttributionSource,
  type BillingCadence,
  type ChildGender,
  type ConsentChoice,
  type EnrollmentYear,
  type FundingType,
  type GradeInterest,
  type GtUsage,
  type HouseholdIncome,
  type NumChildren,
  type ProductInterest,
  type Region,
  type Relationship,
  type SchoolSituation,
  type UsState,
  type YesNo,
} from './lib/options';
import {
  resetNavSeq,
  useStepTelemetry,
  type FormTelemetry,
} from './lib/telemetry';
import { Dropdown } from './steps/Dropdown';
import { RadioGroup } from './steps/RadioGroup';
import { Section } from './steps/Section';
import { SignatureBlock } from './steps/SignatureBlock';

type StepName =
  | 'landing'
  | 'candidacy'
  | 'apply'
  | 'enroll'
  | 'tuition'
  | 'dashboard';
const FLOW_ORDER: StepName[] = ['candidacy', 'apply', 'enroll', 'tuition'];
const STEP_LABELS: { name: StepName; label: string }[] = [
  { name: 'candidacy', label: 'Interest' },
  { name: 'apply', label: 'Apply' },
  { name: 'enroll', label: 'Enroll' },
  { name: 'tuition', label: 'Tuition' },
];

function BrandHeader() {
  return (
    <div className="brand">
      <div className="mark">GT</div>
      <div className="wordmark">
        <span className="gt">GT</span>
        <span className="suffix"> anywhere</span>
      </div>
    </div>
  );
}

function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="footer-brand">
        <span className="gt">GT</span>
        <span className="suffix"> anywhere</span>
      </div>
      <div className="footer-contact">
        © 2026 GT Anywhere · Part of the 2 Hour Learning Network ·{' '}
        <a href="mailto:admissions@gt.school">admissions@gt.school</a>
      </div>
    </footer>
  );
}

export function App({ supabase }: { supabase: MinimalSupabase }) {
  const [uid, setUid] = useState<string | null>(null);
  const [session, setSession] = useState<ApplySession | null>(null);
  const [step, setStep] = useState<StepName>('landing');
  const [fatal, setFatal] = useState<string | null>(null);

  // Sign in anonymously on load (mirrors gtschool's OTP gate → an auth.uid()).
  // The owning family_record is created when the applicant actually starts an
  // application (Begin Application → candidacy), so an empty session shows the
  // dashboard with no cards rather than a phantom record.
  useEffect(() => {
    resetNavSeq();
    let cancelled = false;
    (async () => {
      try {
        const id = await ensureAnonSession(supabase);
        if (!cancelled) setUid(id);
      } catch (e) {
        if (!cancelled) setFatal((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [supabase]);

  // Begin / Add Another Child: create a fresh family_record under the SAME uid
  // and enter the candidacy modal.
  const startApplication = useCallback(async () => {
    if (!uid) return;
    try {
      const sess = await createFamily(supabase, uid, 'direct');
      setSession(sess);
      setStep('candidacy');
    } catch (e) {
      setFatal((e as Error).message);
    }
  }, [supabase, uid]);

  const inFlow = FLOW_ORDER.includes(step);
  const stepIndex = FLOW_ORDER.indexOf(step);

  return (
    <div className="shell">
      <BrandHeader />
      <div className="synthetic-banner">
        Synthetic demo — no real personal information is collected or stored. Every
        identity is generated; selections are structural only.
      </div>

      {/* The labelled stepper is only shown while inside the 4-step flow. */}
      {inFlow && (
        <ol className="stepper" aria-label="progress">
          {STEP_LABELS.map(({ name, label }, i) => {
            const idx = FLOW_ORDER.indexOf(name);
            const state =
              idx < stepIndex ? 'done' : idx === stepIndex ? 'active' : '';
            return (
              <li key={name} className={'stepper-node ' + state}>
                <span className="stepper-dot" aria-hidden="true">
                  {idx < stepIndex ? '✓' : i + 1}
                </span>
                <span className="stepper-label">{label}</span>
              </li>
            );
          })}
        </ol>
      )}

      {fatal && <div className="card err">Could not start application: {fatal}</div>}

      {!fatal && step === 'landing' && (
        <LandingStep
          supabase={supabase}
          uid={uid}
          onBegin={startApplication}
          onDashboard={() => setStep('dashboard')}
        />
      )}

      {!fatal && step === 'candidacy' && session && (
        <CandidacyStep
          supabase={supabase}
          session={session}
          onNext={() => setStep('apply')}
        />
      )}
      {!fatal && step === 'apply' && session && (
        <ApplyStep
          supabase={supabase}
          session={session}
          onNext={() => setStep('enroll')}
        />
      )}
      {!fatal && step === 'enroll' && session && (
        <EnrollStep
          supabase={supabase}
          session={session}
          onNext={() => setStep('tuition')}
        />
      )}
      {!fatal && step === 'tuition' && session && (
        <TuitionStep
          supabase={supabase}
          session={session}
          onNext={() => {
            setSession(null);
            setStep('dashboard');
          }}
        />
      )}
      {!fatal && step === 'dashboard' && (
        <DashboardStep
          supabase={supabase}
          onAddChild={startApplication}
        />
      )}

      {(step === 'landing' || step === 'dashboard') && <SiteFooter />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Marketing landing page — "The MIT of K-8" hero + Begin Application CTA.
// Presentational; no data write. A step_viewed telemetry is fired (no family yet
// → keyed only when a family exists; here it's a presentational view).
// ---------------------------------------------------------------------------
function LandingStep({
  supabase,
  uid,
  onBegin,
  onDashboard,
}: {
  supabase: MinimalSupabase;
  uid: string | null;
  onBegin: () => void;
  onDashboard: () => void;
}) {
  // Presentational telemetry: a landing step_viewed (no family_id yet, so the
  // hook no-ops the write — the view is still rendered). Kept for parity with the
  // brief's "a step_viewed for landing is fine".
  useStepTelemetry(supabase, null, 'landing');
  void uid;

  return (
    <div className="card landing">
      <div className="landing-badges">
        <span className="badge-pill">TEA Approved School</span>
        <span className="badge-pill">Fall 2026 · Now Enrolling</span>
      </div>
      <h1 className="landing-hero">
        The MIT of K–8.
        <span className="landing-hero-accent">
          For students who ask for more academics.
        </span>
      </h1>
      <p className="sub">
        GT Anywhere — the Gifted Academy of Alpha School. A fully online program
        for students who want more.
      </p>
      <div className="actions">
        <button onClick={onDashboard}>My Applications</button>
        <button className="primary" onClick={onBegin} disabled={!uid}>
          {uid ? 'Begin Application' : 'Starting…'}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Candidacy modal — "Secure Your Candidacy for Fall 2026". Mirrors the real
// candidacy form's fields, but with NO typed PII: identity fields are prefilled-
// synthetic + read-only; Household Income is a dropdown; SMS-consent is a
// checkbox. Start Application writes the leads_new row (deriving the columns the
// modal doesn't collect, so the row stays valid + the cockpit derives stage).
// household_income is UI + telemetry only (leads_new has no column for it).
// ---------------------------------------------------------------------------
function CandidacyStep({
  supabase,
  session,
  onNext,
}: {
  supabase: MinimalSupabase;
  session: ApplySession;
  onNext: () => void;
}) {
  const t = useStepTelemetry(supabase, session.familyId, 'candidacy');
  const id = session.identity;
  const [income, setIncome] = useState<HouseholdIncome | ''>('');
  const [smsConsent, setSmsConsent] = useState(false);
  const [errors, setErrors] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);

  async function start() {
    const errs: Record<string, boolean> = {};
    if (!income) errs.household_income = true;
    if (Object.keys(errs).length) {
      setErrors(errs);
      Object.keys(errs).forEach((k) => t.validationError(k));
      return;
    }
    setBusy(true);
    try {
      // DERIVE the leads_new columns the candidacy modal doesn't collect
      // (product/region/grade/num_children) deterministically, so the row stays
      // valid + the cockpit derives stage. household_income is NOT persisted
      // (no column) — it's UI + telemetry only.
      const answers = deriveInterestAnswers(session.familyId, 'direct');
      await submitInterest(supabase, session, answers);
      t.stepCompleted();
      onNext();
    } catch (e) {
      setErrors({ submit: true });
      setBusy(false);
      console.error(e);
    }
  }

  return (
    <div className="card candidacy">
      <h2>Secure Your Candidacy for Fall 2026</h2>
      <p className="sub">
        Your details are pre-filled with a synthetic identity — nothing is typed.
      </p>

      <div className="candidacy-grid">
        <PrefilledField label="First Name" value={id.firstName} />
        <PrefilledField label="Last Name" value={id.lastName} />
      </div>
      <PrefilledField label="Email" value={id.email} fieldKey="email_synthetic" />
      <div className="candidacy-grid">
        <PrefilledField label="Phone" value={id.phone} fieldKey="phone_synthetic" />
        <PrefilledField label="Zip Code" value={id.zip} fieldKey="zip_synthetic" />
      </div>

      <Dropdown
        label="Household Income"
        fieldKey="household_income"
        value={income}
        options={HOUSEHOLD_INCOME}
        labelFor={(o) => HOUSEHOLD_INCOME_LABEL[o]}
        onChange={setIncome}
        telemetry={t}
        error={errors.household_income}
      />

      <label className="check-row">
        <input
          type="checkbox"
          aria-label="sms_consent"
          checked={smsConsent}
          onFocus={() => t.fieldFocused('sms_consent')}
          onChange={(e) => {
            t.fieldChanged('sms_consent');
            setSmsConsent(e.target.checked);
          }}
        />
        I agree to receive SMS messages from 2 Hour Learning regarding inquiry
        follow-up, invitations to events, and personalized updates. Message &amp;
        data rates may apply. Reply STOP to opt out.
      </label>

      {errors.submit && (
        <div className="err">Something went wrong saving — please try again.</div>
      )}
      <div className="actions">
        <span />
        <button className="primary" onClick={start} disabled={busy}>
          {busy ? 'Saving…' : 'Start Application'}
        </button>
      </div>
    </div>
  );
}

// A read-only prefilled identity row for the candidacy modal. NO input element is
// rendered — the value is presentational text — so no PII can be typed (INV-1 by
// shape). Focusing the row fires nothing; it's not an input.
function PrefilledField({
  label,
  value,
  fieldKey,
}: {
  label: string;
  value: string;
  fieldKey?: string;
}) {
  return (
    <div className="prefilled" aria-label={fieldKey ?? label.toLowerCase().replace(/\s+/g, '_')}>
      <span className="prefilled-cap">{label}</span>
      <span className="prefilled-val">{value}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 2 — Apply. ONE long multi-section form. Writes app_form (stage `apply`).
// Identity is the generated synthetic one (shown, never typed). All inputs are
// dropdown/radio/checkbox; the only persisted write remains the unchanged
// app_form row.
// ---------------------------------------------------------------------------
function ApplyStep({
  supabase,
  session,
  onNext,
}: {
  supabase: MinimalSupabase;
  session: ApplySession;
  onNext: () => void;
}) {
  const t = useStepTelemetry(supabase, session.familyId, 'apply');
  const id = session.identity;

  // Program / interest (collected HERE now — the candidacy modal no longer does).
  const [product, setProduct] = useState<ProductInterest | ''>('');
  // Parent/Guardian
  const [relationship1, setRelationship1] = useState<Relationship | ''>('');
  const [hasGuardian2, setHasGuardian2] = useState(false);
  const [relationship2, setRelationship2] = useState<Relationship | ''>('');
  // Address
  const [state, setState] = useState<UsState | ''>('');
  const [region, setRegion] = useState<Region | ''>('');
  // Household & eligibility
  const [numChildren, setNumChildren] = useState<NumChildren | ''>('');
  const [receivedTefa, setReceivedTefa] = useState<YesNo | ''>('');
  // Child information
  const [childGender, setChildGender] = useState<ChildGender | ''>('');
  const [childGrade, setChildGrade] = useState<GradeInterest | ''>('');
  const [enrollYear, setEnrollYear] = useState<EnrollmentYear | ''>('');
  const [schoolSituation, setSchoolSituation] = useState<SchoolSituation | ''>('');
  const [usage, setUsage] = useState<GtUsage | ''>('');
  const [iepPlan, setIepPlan] = useState<YesNo | ''>('');
  const [disabilities, setDisabilities] = useState<YesNo | ''>('');
  const [childAck, setChildAck] = useState(false);
  // Consents
  const [tuitionAware, setTuitionAware] = useState(false);
  // Attribution
  const [heardAbout, setHeardAbout] = useState<AttributionSource | ''>('');

  const [errors, setErrors] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);

  async function next() {
    const errs: Record<string, boolean> = {};
    if (!product) errs.product_interest = true;
    if (!relationship1) errs.relationship = true;
    if (hasGuardian2 && !relationship2) errs.relationship_2 = true;
    if (!state) errs.state = true;
    if (!region) errs.region = true;
    if (!numChildren) errs.num_children = true;
    if (!receivedTefa) errs.tefa_funds = true;
    if (!childGender) errs.child_gender = true;
    if (!childGrade) errs.child_grade = true;
    if (!enrollYear) errs.enrollment_year = true;
    if (!schoolSituation) errs.school_situation = true;
    if (!usage) errs.gt_usage = true;
    if (!iepPlan) errs.iep_plan = true;
    if (!disabilities) errs.disabilities = true;
    if (!childAck) errs.child_ack = true;
    if (!tuitionAware) errs.tuition_aware = true;
    if (!heardAbout) errs.attribution_source = true;
    if (Object.keys(errs).length) {
      setErrors(errs);
      Object.keys(errs).forEach((k) => t.validationError(k));
      return;
    }
    setBusy(true);
    try {
      await submitApply(supabase, session);
      t.stepCompleted();
      onNext();
    } catch (e) {
      setBusy(false);
      setErrors({ submit: true });
      console.error(e);
    }
  }

  return (
    <div className="card">
      <h2>Your application</h2>
      <p className="sub">
        Applying as <strong>{id.displayName}</strong> (synthetic). Every field is a
        structural choice — nothing is typed.
      </p>

      <Section title="Program">
        <Dropdown
          label="Which program interests you?"
          fieldKey="product_interest"
          value={product}
          options={PRODUCT_INTEREST}
          labelFor={(o) => PRODUCT_INTEREST_LABEL[o]}
          onChange={setProduct}
          telemetry={t}
          error={errors.product_interest}
        />
      </Section>

      <Section title="Parent / Guardian #1">
        <div className="prefilled" aria-label="guardian_1_name">
          <span className="prefilled-cap">Name</span>
          <span className="prefilled-val">
            {id.firstName} {id.lastName}
          </span>
        </div>
        <Dropdown
          label="Relationship to child"
          fieldKey="relationship"
          value={relationship1}
          options={RELATIONSHIP}
          labelFor={(o) => RELATIONSHIP_LABEL[o]}
          onChange={setRelationship1}
          telemetry={t}
          error={errors.relationship}
        />
      </Section>

      <Section title="Parent / Guardian #2">
        <label className="check-row">
          <input
            type="checkbox"
            aria-label="add_guardian_2"
            checked={hasGuardian2}
            onFocus={() => t.fieldFocused('add_guardian_2')}
            onChange={(e) => {
              t.fieldChanged('add_guardian_2');
              setHasGuardian2(e.target.checked);
            }}
          />
          Add a second parent / guardian?
        </label>
        {hasGuardian2 && (
          <Dropdown
            label="Second guardian relationship to child"
            fieldKey="relationship_2"
            value={relationship2}
            options={RELATIONSHIP}
            labelFor={(o) => RELATIONSHIP_LABEL[o]}
            onChange={setRelationship2}
            telemetry={t}
            error={errors.relationship_2}
          />
        )}
      </Section>

      <Section title="Address">
        <Dropdown
          label="State"
          fieldKey="state"
          value={state}
          options={US_STATE}
          onChange={setState}
          telemetry={t}
          error={errors.state}
        />
        <Dropdown
          label="Region"
          fieldKey="region"
          value={region}
          options={REGION}
          onChange={setRegion}
          telemetry={t}
          error={errors.region}
        />
      </Section>

      <Section title="Household & Eligibility">
        <Dropdown
          label="How many children are you enrolling?"
          fieldKey="num_children"
          value={numChildren === '' ? '' : (String(numChildren) as `${NumChildren}`)}
          options={NUM_CHILDREN.map((n) => String(n)) as `${NumChildren}`[]}
          onChange={(v) => setNumChildren(Number(v) as NumChildren)}
          telemetry={t}
          error={errors.num_children}
        />
        <RadioGroup
          label="Have you received TEFA funds before?"
          fieldKey="tefa_funds"
          value={receivedTefa}
          options={YES_NO}
          labelFor={(o) => YES_NO_LABEL[o]}
          onChange={setReceivedTefa}
          telemetry={t}
          error={errors.tefa_funds}
        />
      </Section>

      <Section
        title="Child Information"
        hint="The child is the generated synthetic applicant — no real details."
      >
        <Dropdown
          label="Child gender"
          fieldKey="child_gender"
          value={childGender}
          options={CHILD_GENDER}
          labelFor={(o) => CHILD_GENDER_LABEL[o]}
          onChange={setChildGender}
          telemetry={t}
          error={errors.child_gender}
        />
        <Dropdown
          label="Grade"
          fieldKey="child_grade"
          value={childGrade}
          options={GRADE_INTEREST}
          labelFor={(o) => (o === 'K' ? 'Kindergarten' : `Grade ${o}`)}
          onChange={setChildGrade}
          telemetry={t}
          error={errors.child_grade}
        />
        <Dropdown
          label="Desired enrollment year"
          fieldKey="enrollment_year"
          value={enrollYear}
          options={ENROLLMENT_YEAR}
          onChange={setEnrollYear}
          telemetry={t}
          error={errors.enrollment_year}
        />
        <Dropdown
          label="Current school situation"
          fieldKey="school_situation"
          value={schoolSituation}
          options={SCHOOL_SITUATION}
          labelFor={(o) => SCHOOL_SITUATION_LABEL[o]}
          onChange={setSchoolSituation}
          telemetry={t}
          error={errors.school_situation}
        />
        <Dropdown
          label="How will your child use GT?"
          fieldKey="gt_usage"
          value={usage}
          options={GT_USAGE}
          labelFor={(o) => GT_USAGE_LABEL[o]}
          onChange={setUsage}
          telemetry={t}
          error={errors.gt_usage}
        />
        <RadioGroup
          label="Does your child have an IEP, 504, or behavior plan?"
          fieldKey="iep_plan"
          value={iepPlan}
          options={YES_NO}
          labelFor={(o) => YES_NO_LABEL[o]}
          onChange={setIepPlan}
          telemetry={t}
          error={errors.iep_plan}
        />
        <RadioGroup
          label="Any diagnosed disabilities?"
          fieldKey="disabilities"
          value={disabilities}
          options={YES_NO}
          labelFor={(o) => YES_NO_LABEL[o]}
          onChange={setDisabilities}
          telemetry={t}
          error={errors.disabilities}
        />
        <label className="check-row">
          <input
            type="checkbox"
            aria-label="child_ack"
            checked={childAck}
            onFocus={() => t.fieldFocused('child_ack')}
            onChange={(e) => {
              t.fieldChanged('child_ack');
              setChildAck(e.target.checked);
            }}
          />
          I acknowledge the information above is accurate.
        </label>
        {errors.child_ack && (
          <div className="err">Please acknowledge to continue.</div>
        )}
      </Section>

      <Section title="Consents">
        <label className="check-row">
          <input
            type="checkbox"
            aria-label="tuition_aware"
            checked={tuitionAware}
            onFocus={() => t.fieldFocused('tuition_aware')}
            onChange={(e) => {
              t.fieldChanged('tuition_aware');
              setTuitionAware(e.target.checked);
            }}
          />
          I understand GT Anywhere has tuition and have reviewed the cost.
        </label>
        {errors.tuition_aware && (
          <div className="err">Please confirm tuition awareness to continue.</div>
        )}
      </Section>

      <Section title="How did you hear about us?">
        <Dropdown
          label="Source"
          fieldKey="attribution_source"
          value={heardAbout}
          options={ATTRIBUTION_SOURCE}
          labelFor={(o) => ATTRIBUTION_SOURCE_LABEL[o]}
          onChange={setHeardAbout}
          telemetry={t}
          error={errors.attribution_source}
        />
      </Section>

      {errors.submit && (
        <div className="err">Something went wrong — please try again.</div>
      )}
      <div className="actions">
        <span />
        <button className="primary" onClick={next} disabled={busy}>
          {busy ? 'Submitting…' : 'Submit application'}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 3 — Enroll. A left-rail sub-stepper of 7 forms, each with a structural
// field or two + a signature block. Writes ONE enrollment_forms row on completion
// (stage `enroll`) — the write contract is unchanged; only the UX is deeper. The
// 6 required forms must be signed to continue; media_authorization is optional.
// ---------------------------------------------------------------------------
interface EnrollFormDef {
  form_key: string;
  title: string;
  optional?: boolean;
  /** A lightweight structural body (a dropdown or two), rendered above signature. */
  body?: (t: FormTelemetry) => ReactElement;
}

function ENROLL_FORMS(): EnrollFormDef[] {
  return [
    {
      form_key: 'student_information',
      title: 'Student Information',
      body: (ft) => (
        <ConsentRow
          ft={ft}
          fieldKey="info_confirmed"
          label="Student details confirmed (synthetic)"
        />
      ),
    },
    {
      form_key: 'parent_guardian_information',
      title: 'Parent / Guardian Information',
      body: (ft) => (
        <ConsentRow
          ft={ft}
          fieldKey="contact_confirmed"
          label="Guardian contact confirmed (synthetic)"
        />
      ),
    },
    {
      form_key: 'data_collection_consent',
      title: 'Data Collection Consent',
      body: (ft) => (
        <>
          <ConsentDropdown
            ft={ft}
            fieldKey="privacy_policy"
            label="Privacy policy"
          />
          <ConsentDropdown
            ft={ft}
            fieldKey="av_recording"
            label="Audio / video recording consent"
          />
          <ConsentDropdown
            ft={ft}
            fieldKey="human_review"
            label="Human-review-of-AI consent"
          />
        </>
      ),
    },
    {
      form_key: 'academic_information',
      title: 'Academic Information',
      body: (ft) => (
        <ConsentRow
          ft={ft}
          fieldKey="academic_confirmed"
          label="Prior academic records will be shared on enrollment"
        />
      ),
    },
    {
      form_key: 'privacy_data_consent',
      title: 'Privacy & Data Consent',
      body: (ft) => (
        <ConsentDropdown
          ft={ft}
          fieldKey="data_sharing"
          label="Data-sharing with partners consent"
        />
      ),
    },
    {
      form_key: 'tuition_agreement',
      title: 'Tuition Agreement',
      body: (ft) => <TuitionAgreementBody ft={ft} />,
    },
    {
      form_key: 'media_authorization',
      title: 'Media Authorization (optional)',
      optional: true,
      body: (ft) => (
        <ConsentDropdown
          ft={ft}
          fieldKey="media_release"
          label="Photo / video release"
        />
      ),
    },
  ];
}

// Small shared structural rows used inside the enroll sub-forms.
function ConsentRow({
  ft,
  fieldKey,
  label,
}: {
  ft: FormTelemetry;
  fieldKey: string;
  label: string;
}) {
  return (
    <label className="check-row">
      <input
        type="checkbox"
        aria-label={fieldKey}
        onFocus={() => ft.fieldFocused(fieldKey)}
        onChange={() => ft.fieldChanged(fieldKey)}
      />
      {label}
    </label>
  );
}

function ConsentDropdown({
  ft,
  fieldKey,
  label,
}: {
  ft: FormTelemetry;
  fieldKey: string;
  label: string;
}) {
  const [v, setV] = useState<ConsentChoice | ''>('');
  return (
    <Dropdown
      label={label}
      fieldKey={fieldKey}
      value={v}
      options={CONSENT_CHOICE}
      labelFor={(o) => CONSENT_CHOICE_LABEL[o]}
      onChange={setV}
      telemetry={ft}
    />
  );
}

function TuitionAgreementBody({ ft }: { ft: FormTelemetry }) {
  const [cadence, setCadence] = useState<BillingCadence | ''>('');
  return (
    <>
      <div className="prefilled" aria-label="tuition_base">
        <span className="prefilled-cap">Annual tuition (base)</span>
        <span className="prefilled-val">$10,400</span>
      </div>
      <Dropdown
        label="Billing cadence"
        fieldKey="billing_cadence"
        value={cadence}
        options={BILLING_CADENCE}
        labelFor={(o) => BILLING_CADENCE_LABEL[o]}
        onChange={setCadence}
        telemetry={ft}
      />
    </>
  );
}

function EnrollStep({
  supabase,
  session,
  onNext,
}: {
  supabase: MinimalSupabase;
  session: ApplySession;
  onNext: () => void;
}) {
  const t = useStepTelemetry(supabase, session.familyId, 'enroll');
  const forms = ENROLL_FORMS();
  const [active, setActive] = useState(0);
  const [signed, setSigned] = useState<boolean[]>(() => forms.map(() => false));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(false);

  const requiredTotal = forms.filter((f) => !f.optional).length; // 6
  const requiredSigned = forms.filter((f, i) => !f.optional && signed[i]).length;
  const allRequiredSigned = requiredSigned === requiredTotal;

  function markSigned(i: number) {
    setSigned((prev) => prev.map((v, j) => (j === i ? true : v)));
  }

  async function continueToTuition() {
    if (!allRequiredSigned) {
      t.validationError('enroll_forms_incomplete');
      setErr(true);
      return;
    }
    setBusy(true);
    try {
      // Unchanged write contract: forms_total stays 6 (required); we pass the
      // required signed count.
      await submitEnroll(supabase, session, requiredSigned);
      t.stepCompleted();
      onNext();
    } catch (e) {
      setBusy(false);
      setErr(true);
      console.error(e);
    }
  }

  const activeForm = forms[active]!;

  return (
    <div className="card enroll-card">
      <h2>Complete your enrollment forms</h2>
      <p className="sub">
        {requiredSigned} of {requiredTotal} required forms complete. Sign each as{' '}
        <strong>{session.identity.displayName}</strong> (synthetic — no document is
        generated).
      </p>

      <div className="enroll-layout">
        <nav className="enroll-rail" aria-label="enrollment forms">
          {forms.map((f, i) => (
            <button
              key={f.form_key}
              className={
                'rail-item' +
                (i === active ? ' active' : '') +
                (signed[i] ? ' signed' : '')
              }
              onClick={() => setActive(i)}
            >
              <span className="rail-check" aria-hidden="true">
                {signed[i] ? '✓' : i + 1}
              </span>
              <span className="rail-title">{f.title}</span>
            </button>
          ))}
        </nav>

        <div className="enroll-pane">
          <EnrollFormPane
            key={activeForm.form_key}
            def={activeForm}
            telemetry={t}
            syntheticName={session.identity.displayName}
            alreadySigned={signed[active]!}
            onSigned={() => markSigned(active)}
          />
        </div>
      </div>

      {err && (
        <div className="err">Please sign all required forms to continue.</div>
      )}
      <div className="actions">
        <span />
        <button
          className="primary"
          onClick={continueToTuition}
          disabled={busy || !allRequiredSigned}
        >
          {busy ? 'Saving…' : 'Continue to tuition'}
        </button>
      </div>
    </div>
  );
}

// One enroll sub-form: fires form_viewed on entry, signature/agree field events
// inside the SignatureBlock, and form_completed on submit. Telemetry events
// carry this form's form_key automatically (via t.forForm).
function EnrollFormPane({
  def,
  telemetry,
  syntheticName,
  alreadySigned,
  onSigned,
}: {
  def: EnrollFormDef;
  telemetry: ReturnType<typeof useStepTelemetry>;
  syntheticName: string;
  alreadySigned: boolean;
  onSigned: () => void;
}) {
  const ft = telemetry.forForm(def.form_key);
  const [signed, setSigned] = useState(alreadySigned);
  const [agreed, setAgreed] = useState(alreadySigned);
  const [err, setErr] = useState(false);

  // form_viewed on entering this sub-form.
  useEffect(() => {
    telemetry.formViewed(def.form_key);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [def.form_key]);

  function submitForm() {
    if (!def.optional && (!signed || !agreed)) {
      ft.validationError('signature');
      setErr(true);
      return;
    }
    telemetry.formCompleted(def.form_key);
    onSigned();
  }

  return (
    <div className="enroll-form">
      <h3 className="section-title">{def.title}</h3>
      {def.body?.(ft)}
      <SignatureBlock
        syntheticName={syntheticName}
        signed={signed}
        agreed={agreed}
        onSign={() => setSigned(true)}
        onAgreeChange={setAgreed}
        telemetry={ft}
        error={err}
      />
      <div className="actions">
        <span />
        <button className="primary" onClick={submitForm} disabled={alreadySigned}>
          {alreadySigned ? 'Submitted' : 'Submit form'}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 4 — Tuition. $1,000 deposit (simulated) → forms_signed=6 + unlock.
// ---------------------------------------------------------------------------
function TuitionStep({
  supabase,
  session,
  onNext,
}: {
  supabase: MinimalSupabase;
  session: ApplySession;
  onNext: () => void;
}) {
  const t = useStepTelemetry(supabase, session.familyId, 'tuition');
  const [funding, setFunding] = useState<FundingType | ''>('');
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<Record<string, boolean>>({});

  async function confirm() {
    if (!funding) {
      setErrors({ funding_type: true });
      t.validationError('funding_type');
      return;
    }
    setBusy(true);
    try {
      await submitTuition(supabase, session, funding as FundingType);
      t.stepCompleted();
      onNext();
    } catch (e) {
      setBusy(false);
      setErrors({ submit: true });
      console.error(e);
    }
  }

  return (
    <div className="card">
      <h2>Reserve your spot</h2>
      <p className="sub">
        A $1,000 deposit confirms enrollment (simulated — no real payment is
        taken).
      </p>
      <Dropdown
        label="How will tuition be funded?"
        fieldKey="funding_type"
        value={funding}
        options={FUNDING_TYPE}
        labelFor={(o) => FUNDING_TYPE_LABEL[o]}
        onChange={setFunding}
        telemetry={t}
        error={errors.funding_type}
      />
      <div className="deposit">
        <div className="sub">Enrollment deposit</div>
        <div className="amount">$1,000</div>
      </div>
      {errors.submit && (
        <div className="err">Something went wrong — please try again.</div>
      )}
      <div className="actions">
        <span />
        <button className="primary" onClick={confirm} disabled={busy}>
          {busy ? 'Confirming…' : 'Pay $1,000 deposit'}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// "My Applications" dashboard — the returning-user hub. One card per
// family_record the session created (RLS auto-scopes the query to auth.uid()),
// X/4 stage progress, a delete (trash) control, and "+ Add Another Child".
// ---------------------------------------------------------------------------
const STAGE_LABEL: Record<string, string> = {
  interest: 'Interest',
  apply: 'Application',
  enroll: 'Enrollment',
  tuition: 'Pay Deposit',
};

function DashboardStep({
  supabase,
  onAddChild,
}: {
  supabase: MinimalSupabase;
  onAddChild: () => void;
}) {
  const [apps, setApps] = useState<ApplicationSummary[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const rows = await fetchApplications(supabase);
      setApps(rows);
    } catch (e) {
      setErr((e as Error).message);
    }
  }, [supabase]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function remove(familyId: string) {
    try {
      await deleteApplication(supabase, familyId);
      await refresh();
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  return (
    <div className="card dashboard">
      <h2>My Applications</h2>
      <p className="sub">Welcome back! Manage your applications below.</p>

      {err && <div className="err">Could not load applications: {err}</div>}

      {apps && apps.length === 0 && (
        <p className="dashboard-empty">
          You have no applications yet. Start one below.
        </p>
      )}

      <div className="app-list">
        {(apps ?? []).map((app) => (
          <div className="app-card" key={app.familyId} aria-label="application_card">
            <div className="app-card-main">
              <div className="app-card-name">{app.displayName}</div>
              <div className="app-card-year">{app.schoolYear} School Year</div>
              <div className="app-card-progress">
                <span className="progress-count">
                  {app.stagesComplete}/{app.stagesTotal}
                </span>{' '}
                {STAGE_LABEL[app.currentStage]}
              </div>
            </div>
            <button
              className="trash"
              aria-label={`delete_${app.familyId}`}
              title="Delete application"
              onClick={() => remove(app.familyId)}
            >
              🗑
            </button>
          </div>
        ))}
      </div>

      <div className="actions">
        <span />
        <button className="primary add-child" onClick={onAddChild}>
          + Add Another Child
        </button>
      </div>
    </div>
  );
}
