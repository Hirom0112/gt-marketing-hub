// A labelled <select> — the ONLY value-bearing input primitive in this form.
// There is no text input component anywhere in the app, so PII cannot be typed
// (INV-1 by shape). Telemetry: field_focused on focus, field_left_empty on blur
// with no selection, field_changed when a selection is set. `field_key` is the
// field NAME, never the chosen value. The telemetry source may be a step-level
// or a form-level emitter (both satisfy FormTelemetry), so the same Dropdown is
// reused inside Apply sections and Enroll sub-forms — and the emitted event
// carries the right form_key automatically.

import type { FormTelemetry } from '../lib/telemetry';

interface DropdownProps<T extends string> {
  label: string;
  fieldKey: string;
  value: T | '';
  options: readonly T[];
  labelFor?: (opt: T) => string;
  onChange: (v: T) => void;
  telemetry: FormTelemetry;
  error?: boolean;
}

export function Dropdown<T extends string>({
  label,
  fieldKey,
  value,
  options,
  labelFor,
  onChange,
  telemetry,
  error,
}: DropdownProps<T>) {
  return (
    <div className="field">
      <label htmlFor={fieldKey}>{label}</label>
      <select
        id={fieldKey}
        aria-label={label}
        data-error={error ? 'true' : undefined}
        value={value}
        onFocus={() => telemetry.fieldFocused(fieldKey)}
        onBlur={() => {
          if (value === '') telemetry.fieldLeftEmpty(fieldKey);
        }}
        onChange={(e) => {
          telemetry.fieldChanged(fieldKey);
          onChange(e.target.value as T);
        }}
      >
        <option value="" disabled>
          Select…
        </option>
        {options.map((opt) => (
          <option key={opt} value={opt}>
            {labelFor ? labelFor(opt) : opt}
          </option>
        ))}
      </select>
      {error && <div className="err">Please choose an option to continue.</div>}
    </div>
  );
}
