'use client';

// Dispatches a module id to its screen, enforcing the view gate first.
// Home is the composable dashboard; the rest currently render the spec-faithful
// "module brief" screen (owner, summary, KPI stats, cross-links, what's broken)
// and get deepened into full sub-views slice by slice.

import { moduleById, type ModuleId } from '@/lib/registry';
import { HomeModule } from './modules/HomeModule';
import { DecisionModule } from './modules/DecisionModule';
import { BudgetModule } from './modules/BudgetModule';
import { DashboardModule } from './modules/DashboardModule';
import { CrmModule } from './modules/CrmModule';
import { NurtureModule } from './modules/NurtureModule';
import { CampModule } from './modules/CampModule';
import { GrassrootsModule } from './modules/GrassrootsModule';
import { EventsModule } from './modules/EventsModule';
import { ContentModule } from './modules/ContentModule';
import { AdmissionsModule } from './modules/AdmissionsModule';
import { WebsiteModule } from './modules/WebsiteModule';
import { GenericModule } from './modules/GenericModule';

export function ModuleRouter({ id }: { id: ModuleId }) {
  const def = moduleById(id);
  if (!def) return null;

  if (id === 'home') return <HomeModule />;
  if (id === 'decision') return <DecisionModule />;
  if (id === 'budget') return <BudgetModule />;
  if (id === 'dashboard') return <DashboardModule />;
  if (id === 'crm') return <CrmModule />;
  if (id === 'nurture') return <NurtureModule />;
  if (id === 'camp') return <CampModule />;
  if (id === 'grassroots') return <GrassrootsModule />;
  if (id === 'events') return <EventsModule />;
  if (id === 'content') return <ContentModule />;
  if (id === 'admissions') return <AdmissionsModule />;
  if (id === 'website') return <WebsiteModule />;
  return <GenericModule def={def} />;
}
