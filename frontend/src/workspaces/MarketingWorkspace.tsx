import { useState } from 'react';
import { Globe, Megaphone, Send, Sparkles } from 'lucide-react';
import ContentWorkspace from '../marketing/ContentWorkspace';
import GeoBoard from '../marketing/GeoBoard';
import MarketingBreadth from '../marketing/MarketingBreadth';
import PublishMonitor from '../marketing/PublishMonitor';
import { TabBar, type TabItem } from '../ui';

// S8 Wave 1 marketing workspace — a thin container with a sub-tab bar switching
// between the existing marketing surfaces. Components are mounted unchanged
// (Wave 2 re-skins their internals).
type MarketingTab = 'content' | 'geo' | 'breadth' | 'publish';

const TABS: ReadonlyArray<TabItem<MarketingTab>> = [
  { key: 'content', label: 'Content', icon: Sparkles },
  { key: 'geo', label: 'GEO', icon: Globe },
  { key: 'breadth', label: 'Breadth', icon: Megaphone },
  { key: 'publish', label: 'Publish & Monitor', icon: Send },
];

export default function MarketingWorkspace(): JSX.Element {
  const [tab, setTab] = useState<MarketingTab>('content');
  return (
    <section
      aria-label="Marketing workspace"
      className="marketing-workspace"
      style={{ display: 'grid', gap: 'var(--s-4)' }}
    >
      <TabBar
        tabs={TABS}
        active={tab}
        onSelect={setTab}
        ariaLabel="Marketing sections"
      />
      {tab === 'content' && <ContentWorkspace />}
      {tab === 'geo' && <GeoBoard />}
      {tab === 'breadth' && <MarketingBreadth />}
      {tab === 'publish' && <PublishMonitor />}
    </section>
  );
}
