// A labelled <select> — the ONLY input primitive in this form. There is no text
// input component anywhere in the app, so PII cannot be typed (INV-1 by shape).
// Telemetry: fires field_focused on focus and field_left_empty on blur with no
// selection. `field_key` is the field NAME, never the chosen value.

import type { StepTelemetry } from '../lib/telemetry';

interface DropdownProps<T extends string> {
  label: string;
  fieldKey: string;
  value: T | '';
  options: readonly T[];
  labelFor?: (opt: T) => string;
  onChange: (v: T) => void;
  telemetry: StepTelemetry;
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
        value={value}
        onFocus={() => telemetry.fieldFocused(fieldKey)}
        onBlur={() => {
          if (value === '') telemetry.fieldLeftEmpty(fieldKey);
        }}
        onChange={(e) => onChange(e.target.value as T)}
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
