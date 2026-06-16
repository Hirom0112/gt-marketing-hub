// The mock apply SPA — a single-page 4-node stepper (no react-router), mirroring
// gtschool's apply flow with faithful structure / trimmed depth.
//
// Flow & row-writing order (so the cockpit join works):
//   sign-in (anon) → create family_record
//   Interest  → INSERT leads_new          (family visible in cockpit here)
//   Apply     → INSERT app_form           (derives stage `apply`)
//   Enroll    → INSERT enrollment_forms   (derives stage `enroll`)
//   Tuition   → $1,000 deposit → forms_signed=6 + tuition_unlocked (stage `tuition`)
//
// Everything is dropdown/checkbox/radio — no free-typed PII (INV-1/INV-6).

import { useEffect, useState } from 'react';
import {
  createFamily,
  ensureAnonSession,
  submitApply,
  submitEnroll,
  submitInterest,
  submitTuition,
  type ApplySession,
  type InterestAnswers,
  type MinimalSupabase,
} from './lib/apply';
import {
  ATTRIBUTION_SOURCE,
  ATTRIBUTION_SOURCE_LABEL,
  FUNDING_TYPE,
  FUNDING_TYPE_LABEL,
  GRADE_INTEREST,
  NUM_CHILDREN,
  PRODUCT_INTEREST,
  PRODUCT_INTEREST_LABEL,
  REGION,
  type AttributionSource,
  type FundingType,
  type GradeInterest,
  type NumChildren,
  type ProductInterest,
  type Region,
} from './lib/options';
import { useStepTelemetry } from './lib/telemetry';
import { Dropdown } from './steps/Dropdown';

type StepName = 'interest' | 'apply' | 'enroll' | 'tuition' | 'done';
const ORDER: StepName[] = ['interest', 'apply', 'enroll', 'tuition', 'done'];

export function App({ supabase }: { supabase: MinimalSupabase }) {
  const [session, setSession] = useState<ApplySession | null>(null);
  const [step, setStep] = useState<StepName>('interest');
  const [fatal, setFatal] = useState<string | null>(null);

  // Sign in anonymously + create the owning family_record on load. This mirrors
  // gtschool's OTP gate (yields an auth.uid()); the SPA uses the anon key only.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const uid = await ensureAnonSession(supabase);
        // family_record needs an attribution_source up front; we seed it with a
        // neutral default and the Interest step records the real choice on the
        // lead. (Insert-only flow: family_record is written once.)
        const sess = await createFamily(supabase, uid, 'direct');
        if (!cancelled) setSession(sess);
      } catch (e) {
        if (!cancelled) setFatal((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [supabase]);

  const stepIndex = ORDER.indexOf(step);

  return (
    <div className="shell">
      <div className="brand">
        <div className="mark">GT</div>
        <h1>Apply to GT School</h1>
      </div>
      <div className="synthetic-banner">
        Synthetic demo — no real personal information is collected or stored. Every
        identity is generated; selections are structural only.
      </div>

      <div className="steps" aria-label="progress">
        {(['interest', 'apply', 'enroll', 'tuition'] as const).map((s, i) => (
          <div
            key={s}
            className={
              'pip' +
              (i < stepIndex ? ' done' : i === stepIndex ? ' active' : '')
            }
          />
        ))}
      </div>

      {fatal && <div className="card err">Could not start application: {fatal}</div>}

      {!fatal && !session && (
        <div className="card">
          <p>Starting your application…</p>
        </div>
      )}

      {session && step === 'interest' && (
        <InterestStep
          supabase={supabase}
          session={session}
          onNext={() => setStep('apply')}
        />
      )}
      {session && step === 'apply' && (
        <ApplyStep
          supabase={supabase}
          session={session}
          onNext={() => setStep('enroll')}
        />
      )}
      {session && step === 'enroll' && (
        <EnrollStep
          supabase={supabase}
          session={session}
          onNext={() => setStep('tuition')}
        />
      )}
      {session && step === 'tuition' && (
        <TuitionStep
          supabase={supabase}
          session={session}
          onNext={() => setStep('done')}
        />
      )}
      {session && step === 'done' && <DoneStep session={session} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 1 — Interest. Writes leads_new (family becomes cockpit-visible here).
// ---------------------------------------------------------------------------
function InterestStep({
  supabase,
  session,
  onNext,
}: {
  supabase: MinimalSupabase;
  session: ApplySession;
  onNext: () => void;
}) {
  const t = useStepTelemetry(supabase, session.familyId, 'interest');
  const [product, setProduct] = useState<ProductInterest | ''>('');
  const [attribution, setAttribution] = useState<AttributionSource | ''>('');
  const [region, setRegion] = useState<Region | ''>('');
  const [grade, setGrade] = useState<GradeInterest | ''>('');
  const [numChildren, setNumChildren] = useState<NumChildren | ''>('');
  const [errors, setErrors] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);

  async function next() {
    const errs: Record<string, boolean> = {};
    if (!product) errs.product_interest = true;
    if (!attribution) errs.attribution_source = true;
    if (!region) errs.region = true;
    if (!grade) errs.grade_interest = true;
    if (!numChildren) errs.num_children = true;
    if (Object.keys(errs).length) {
      setErrors(errs);
      Object.keys(errs).forEach((k) => t.validationError(k));
      return;
    }
    setBusy(true);
    const answers: InterestAnswers = {
      product_interest: product as ProductInterest,
      attribution_source: attribution as AttributionSource,
      region: region as Region,
      grade_interest: grade as GradeInterest,
      num_children: numChildren as NumChildren,
    };
    try {
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
    <div className="card">
      <h2>Tell us about your interest</h2>
      <p className="sub">A few quick questions to get started.</p>
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
      <Dropdown
        label="How many children are you applying for?"
        fieldKey="num_children"
        value={numChildren === '' ? '' : (String(numChildren) as `${NumChildren}`)}
        options={NUM_CHILDREN.map((n) => String(n)) as `${NumChildren}`[]}
        onChange={(v) => setNumChildren(Number(v) as NumChildren)}
        telemetry={t}
        error={errors.num_children}
      />
      <Dropdown
        label="What grade are they entering?"
        fieldKey="grade_interest"
        value={grade}
        options={GRADE_INTEREST}
        labelFor={(o) => (o === 'K' ? 'Kindergarten' : `Grade ${o}`)}
        onChange={setGrade}
        telemetry={t}
        error={errors.grade_interest}
      />
      <Dropdown
        label="Which region are you in?"
        fieldKey="region"
        value={region}
        options={REGION}
        onChange={setRegion}
        telemetry={t}
        error={errors.region}
      />
      <Dropdown
        label="How did you hear about us?"
        fieldKey="attribution_source"
        value={attribution}
        options={ATTRIBUTION_SOURCE}
        labelFor={(o) => ATTRIBUTION_SOURCE_LABEL[o]}
        onChange={setAttribution}
        telemetry={t}
        error={errors.attribution_source}
      />
      {errors.submit && (
        <div className="err">Something went wrong saving — please try again.</div>
      )}
      <div className="actions">
        <span />
        <button className="primary" onClick={next} disabled={busy}>
          {busy ? 'Saving…' : 'Continue to application'}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 2 — Apply. Writes app_form (derives stage `apply`).
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
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(false);

  async function next() {
    if (!ack) {
      t.validationError('application_ack');
      setErr(true);
      return;
    }
    setBusy(true);
    try {
      await submitApply(supabase, session);
      t.stepCompleted();
      onNext();
    } catch (e) {
      setBusy(false);
      setErr(true);
      console.error(e);
    }
  }

  return (
    <div className="card">
      <h2>Your application</h2>
      <p className="sub">
        In the real flow this is the full application + assessment. For this demo,
        confirm the structural details and submit.
      </p>
      <label className="check-row">
        <input
          type="checkbox"
          checked={ack}
          aria-label="application_ack"
          onFocus={() => t.fieldFocused('application_ack')}
          onChange={(e) => setAck(e.target.checked)}
        />
        I confirm the application details are complete and ready to submit.
      </label>
      {err && (
        <div className="err">
          Please confirm above to submit your application.
        </div>
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
// Step 3 — Enroll. Six sign-steps; writes enrollment_forms (derives `enroll`).
// ---------------------------------------------------------------------------
const ENROLL_FORMS = [
  'Enrollment agreement',
  'Family handbook acknowledgement',
  'Media & photo release',
  'Health & emergency contact',
  'Technology use policy',
  'Tuition & payment terms',
];

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
  const [signed, setSigned] = useState<boolean[]>(
    () => ENROLL_FORMS.map(() => false),
  );
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(false);
  const count = signed.filter(Boolean).length;

  function sign(i: number) {
    setSigned((prev) => prev.map((v, j) => (j === i ? true : v)));
    t.fieldFocused(`enroll_form_${i + 1}`);
  }

  async function next() {
    if (count < ENROLL_FORMS.length) {
      t.validationError('enroll_forms_incomplete');
      setErr(true);
      return;
    }
    setBusy(true);
    try {
      await submitEnroll(supabase, session, count);
      t.stepCompleted();
      onNext();
    } catch (e) {
      setBusy(false);
      setErr(true);
      console.error(e);
    }
  }

  return (
    <div className="card">
      <h2>Sign your enrollment forms</h2>
      <p className="sub">
        {count} of {ENROLL_FORMS.length} signed. Tap each to sign (simulated — no
        document is generated).
      </p>
      <div className="form-list">
        {ENROLL_FORMS.map((name, i) => (
          <div
            key={name}
            className={'form-item' + (signed[i] ? ' signed' : '')}
          >
            <span>{name}</span>
            {signed[i] ? (
              <span className="badge signed">Signed</span>
            ) : (
              <button onClick={() => sign(i)}>Sign</button>
            )}
          </div>
        ))}
      </div>
      {err && <div className="err">Please sign all six forms to continue.</div>}
      <div className="actions">
        <span />
        <button className="primary" onClick={next} disabled={busy}>
          {busy ? 'Saving…' : 'Continue to tuition'}
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
// Done.
// ---------------------------------------------------------------------------
function DoneStep({ session }: { session: ApplySession }) {
  return (
    <div className="card done-screen">
      <div className="check">✓</div>
      <h2>You&apos;re enrolled</h2>
      <p className="sub">
        {session.identity.displayName} is all set. Your application now flows into
        the GT growth cockpit.
      </p>
      <div className="synthetic-id">synthetic family · {session.identity.email}</div>
    </div>
  );
}
