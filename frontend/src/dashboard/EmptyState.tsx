import type { ReactNode } from 'react';

// The one shared empty state for the redesign: an unselected detail panel, or a
// tab with no rows. Clean and quiet — an icon, a title, one line of body. Both
// briefs require every tab AND the right panel to have a clean empty state.
export interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  body?: ReactNode;
}

export function EmptyState({ icon, title, body }: EmptyStateProps): JSX.Element {
  return (
    <div className="admin-empty" data-testid="empty-state">
      {icon ? (
        <div className="admin-empty-icon" data-testid="empty-state-icon">
          {icon}
        </div>
      ) : null}
      <div className="admin-empty-title">{title}</div>
      {body ? <div className="admin-empty-body">{body}</div> : null}
    </div>
  );
}

export default EmptyState;
