import { notFound } from 'next/navigation';
import { MODULES, moduleById } from '@/lib/registry';
import { ModuleRouter } from '@/components/ModuleRouter';

// One real route per module (/grassroots, /content, …). Static params come from
// the registry so adding a module is a one-line registry change.
export function generateStaticParams() {
  return MODULES.map((m) => ({ module: m.id }));
}

export default function ModulePage({ params }: { params: { module: string } }) {
  const def = moduleById(params.module);
  if (!def) notFound();
  return <ModuleRouter id={def.id} />;
}
