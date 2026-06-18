import type { ReactNode } from 'react';

// The shared shell both dashboards (admin + sales-agent) compose. It is purely
// presentational and slot-only: it enforces the briefs' restraint rule
// STRUCTURALLY — the ONLY things that render are the KPI strip (top, full width),
// an optional banner (sales-agent daily quote), and the two columns (left work
// area + right detail panel). Nothing renders above the strip; nothing renders
// below the two columns. No data logic lives here — the shells pass the slots.
export interface DashboardLayoutProps {
  kpiStrip: ReactNode;
  banner?: ReactNode;
  tabBar: ReactNode;
  tabPanel: ReactNode;
  detailPanel: ReactNode;
}

export function DashboardLayout({
  kpiStrip,
  banner,
  tabBar,
  tabPanel,
  detailPanel,
}: DashboardLayoutProps): JSX.Element {
  return (
    <div className="admin-dashboard" data-testid="dashboard-layout">
      <div className="admin-kpi-strip" data-testid="dashboard-kpi-strip">
        {kpiStrip}
      </div>
      {banner ? (
        <div className="dash-banner-slot" data-testid="dashboard-banner">
          {banner}
        </div>
      ) : null}
      <div className="admin-grid" data-testid="dashboard-grid">
        <div className="admin-left" data-testid="dashboard-left">
          {tabBar}
          {tabPanel}
        </div>
        <div className="admin-right" data-testid="dashboard-right">
          {detailPanel}
        </div>
      </div>
    </div>
  );
}

export default DashboardLayout;
