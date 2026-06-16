// A titled section within the long Apply form. Pure layout — no inputs of its
// own, so it introduces no PII surface.

import type { ReactNode } from 'react';

export function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <section className="section">
      <h3 className="section-title">{title}</h3>
      {hint && <p className="section-hint">{hint}</p>}
      {children}
    </section>
  );
}
