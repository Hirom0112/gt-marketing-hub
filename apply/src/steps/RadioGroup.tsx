// A labelled radio group — a structural choice primitive (yes/no eligibility +
// consent radios). Like Dropdown it carries NO free text: the choices are a
// closed option set, so INV-1/INV-6 hold by shape. Fires field_changed on
// selection and field_focused on focus; field_key is the field NAME, not the
// chosen value.

import type { FormTelemetry } from '../lib/telemetry';

interface RadioGroupProps<T extends string> {
  label: string;
  fieldKey: string;
  value: T | '';
  options: readonly T[];
  labelFor?: (opt: T) => string;
  onChange: (v: T) => void;
  telemetry: FormTelemetry;
  error?: boolean;
}

export function RadioGroup<T extends string>({
  label,
  fieldKey,
  value,
  options,
  labelFor,
  onChange,
  telemetry,
  error,
}: RadioGroupProps<T>) {
  return (
    <div className="field" role="group" aria-label={label}>
      <span className="radio-label">{label}</span>
      <div className="radio-row" data-error={error ? 'true' : undefined}>
        {options.map((opt) => (
          <label key={opt} className={'radio-pill' + (value === opt ? ' on' : '')}>
            <input
              type="radio"
              name={fieldKey}
              value={opt}
              checked={value === opt}
              onFocus={() => telemetry.fieldFocused(fieldKey)}
              onChange={() => {
                telemetry.fieldChanged(fieldKey);
                onChange(opt);
              }}
            />
            {labelFor ? labelFor(opt) : opt}
          </label>
        ))}
      </div>
      {error && <div className="err">Please choose an option to continue.</div>}
    </div>
  );
}
