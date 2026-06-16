// The signature block shared by all Enroll sub-forms. The signature is the
// pre-filled SYNTHETIC household name (read-only, never typed) — clicking "Sign
// as <name>" stamps it; an "I agree to the terms above" checkbox + an auto
// today's date complete the block. There is NO free-text input: the only inputs
// are a button and a checkbox, so no PII can be entered (INV-1 by shape; the
// name shown is the generated synthetic identity, INV-6-safe).
//
// Telemetry: focusing/stamping the signature fires field_changed("signature");
// the agree checkbox fires field_changed("agree_terms"). field_key is the field
// NAME, never the synthetic value.

import type { FormTelemetry } from '../lib/telemetry';

const TODAY = new Date().toLocaleDateString('en-US', {
  year: 'numeric',
  month: 'long',
  day: 'numeric',
});

export function SignatureBlock({
  syntheticName,
  signed,
  agreed,
  onSign,
  onAgreeChange,
  telemetry,
  error,
}: {
  syntheticName: string;
  signed: boolean;
  agreed: boolean;
  onSign: () => void;
  onAgreeChange: (v: boolean) => void;
  telemetry: FormTelemetry;
  error?: boolean;
}) {
  return (
    <div className="signature-block">
      <div className="sig-row">
        <div className="sig-line">
          <span className="sig-caption">Signature</span>
          {signed ? (
            <span className="sig-name" aria-label="signature">
              {syntheticName}
            </span>
          ) : (
            <span className="sig-placeholder" aria-hidden="true">
              ——————————
            </span>
          )}
        </div>
        <div className="sig-line">
          <span className="sig-caption">Date</span>
          <span className="sig-date">{signed ? TODAY : '—'}</span>
        </div>
      </div>
      {!signed ? (
        <button
          type="button"
          className="sign-btn"
          aria-label="signature"
          onClick={() => {
            telemetry.fieldChanged('signature');
            onSign();
          }}
        >
          Sign as {syntheticName}
        </button>
      ) : (
        <span className="badge signed">Signed</span>
      )}
      <label className="check-row agree-row">
        <input
          type="checkbox"
          aria-label="agree_terms"
          checked={agreed}
          disabled={!signed}
          onFocus={() => telemetry.fieldFocused('agree_terms')}
          onChange={(e) => {
            telemetry.fieldChanged('agree_terms');
            onAgreeChange(e.target.checked);
          }}
        />
        I agree to the terms above.
      </label>
      {error && (
        <div className="err">Please sign and agree to the terms to continue.</div>
      )}
    </div>
  );
}
