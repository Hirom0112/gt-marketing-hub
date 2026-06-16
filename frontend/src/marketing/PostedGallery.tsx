import { useEffect, useState } from 'react';
import {
  ArrowLeft,
  AtSign,
  Clock,
  Globe,
  Hash,
  Image as ImageIcon,
  Play,
  Share2,
  Star,
  Video,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { apiBaseUrl } from '../config';
import { Card, Chip } from '../ui';

// Posted-content gallery (FR-3.4) — "see every picture we ever posted and WHERE,
// and the WORDS along with it." The kept+validated library posts grouped by the
// platform they came FROM (GET /content/gallery). The flow is:
//   platform tiles (with per-platform counts)
//     → click a tile → GET /content/gallery?platform=<p> → a grid of post cards
//        (image placeholder + caption + posted date + value badge)
//     → a Most valuable / Most recent sort toggle (re-fetches with ?sort=)
//     → a back-to-all affordance returns to the tiles.
//
// The image is a PLACEHOLDER ref in v1 (media-gen isn't wired yet); value +
// posted_at are deterministic synthetic placeholders (no real engagement feed).
// Read-only (the deterministic core owns all writes, INV-2); native fetch only.

// One platform tile: the platform + how many posts it holds.
interface PlatformGroup {
  platform: string;
  count: number;
}

// One post card: the picture (placeholder), the words, where + when + value.
interface PostItem {
  id: string;
  platform: string;
  asset_type: string;
  caption: string;
  image_ref: string;
  posted_at: string;
  value: number;
}

// GET /content/gallery response — tiles when no platform, posts when drilled in.
interface GalleryView {
  groups: PlatformGroup[];
  posts: PostItem[];
}

type SortKey = 'most_valuable' | 'most_recent';

type GalleryState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: GalleryView };

// Per-platform icon + a human label. The origin tag is the wire key (the "WHERE");
// FB/YouTube collapse onto the Instagram channel server-side but the tag is faithful.
const PLATFORM_META: Record<string, { icon: LucideIcon; label: string }> = {
  facebook: { icon: Share2, label: 'Facebook' },
  instagram: { icon: AtSign, label: 'Instagram' },
  'x/twitter': { icon: Hash, label: 'X / Twitter' },
  youtube: { icon: Play, label: 'YouTube' },
  tiktok: { icon: Video, label: 'TikTok' },
  linkedin: { icon: Globe, label: 'LinkedIn' },
};

function platformLabel(platform: string): string {
  return PLATFORM_META[platform]?.label ?? platform;
}

function PlatformIcon({ platform }: { platform: string }): JSX.Element {
  const Icon = PLATFORM_META[platform]?.icon ?? ImageIcon;
  return <Icon size={18} aria-hidden style={{ color: 'var(--flow)' }} />;
}

export default function PostedGallery(): JSX.Element {
  // null = the tiles landing; a string = the platform drilled into.
  const [platform, setPlatform] = useState<string | null>(null);
  const [sort, setSort] = useState<SortKey>('most_recent');
  const [state, setState] = useState<GalleryState>({ status: 'loading' });

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading' });
    const qs = new URLSearchParams();
    if (platform) {
      qs.set('platform', platform);
      qs.set('sort', sort);
    }
    fetch(`${apiBaseUrl}/content/gallery?${qs.toString()}`)
      .then((res) => {
        if (!res.ok) throw new Error(`gallery request failed: ${res.status}`);
        return res.json() as Promise<Partial<GalleryView>>;
      })
      .then((raw) => {
        // Coerce a missing/malformed shape to empty (degrade cleanly, never crash).
        const data: GalleryView = {
          groups: Array.isArray(raw?.groups) ? raw.groups : [],
          posts: Array.isArray(raw?.posts) ? raw.posts : [],
        };
        if (!cancelled) setState({ status: 'ready', data });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : 'unknown error';
          setState({ status: 'error', message });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [platform, sort]);

  return (
    <Card
      className="posted-gallery"
      data-testid="posted-gallery"
      style={{ display: 'grid', gap: 'var(--s-3)' }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--s-2)' }}>
        <ImageIcon size={15} aria-hidden style={{ color: 'var(--flow)' }} />
        <h3 style={{ fontSize: 'var(--fs-md)', fontWeight: 600, margin: 0 }}>
          Posted gallery
        </h3>
        <span className="lab" style={{ marginLeft: 'auto' }}>
          Every post we made — by platform, ranked
        </span>
      </div>

      {state.status === 'loading' && (
        <p data-testid="gallery-loading" className="lab">
          Loading gallery…
        </p>
      )}
      {state.status === 'error' && (
        <p
          data-testid="gallery-error"
          role="alert"
          style={{ color: 'var(--signal-ink)', margin: 0 }}
        >
          Could not load gallery: {state.message}
        </p>
      )}

      {state.status === 'ready' && platform === null && (
        <PlatformTiles groups={state.data.groups} onPick={setPlatform} />
      )}

      {state.status === 'ready' && platform !== null && (
        <PlatformPosts
          platform={platform}
          posts={state.data.posts}
          sort={sort}
          onSort={setSort}
          onBack={() => {
            setPlatform(null);
            setSort('most_recent');
          }}
        />
      )}
    </Card>
  );
}

// The platform tiles landing: one tile per platform with its post count.
function PlatformTiles({
  groups,
  onPick,
}: {
  groups: PlatformGroup[];
  onPick: (platform: string) => void;
}): JSX.Element {
  if (groups.length === 0) {
    return (
      <p
        data-testid="gallery-empty"
        style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', margin: 0 }}
      >
        No posted content yet.
      </p>
    );
  }
  return (
    <div
      data-testid="gallery-tiles"
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))',
        gap: 'var(--s-2)',
      }}
    >
      {groups.map((g) => (
        <button
          key={g.platform}
          type="button"
          data-testid={`gallery-tile-${g.platform}`}
          onClick={() => onPick(g.platform)}
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'flex-start',
            gap: 'var(--s-2)',
            padding: 'var(--s-3)',
            borderRadius: 'var(--r-md)',
            border: '1px solid var(--line)',
            background: 'var(--surface-2)',
            color: 'var(--ink)',
            cursor: 'pointer',
            textAlign: 'left',
          }}
        >
          <PlatformIcon platform={g.platform} />
          <span style={{ fontSize: 'var(--fs-sm)', fontWeight: 600 }}>
            {platformLabel(g.platform)}
          </span>
          <Chip tone="flow">{g.count}</Chip>
        </button>
      ))}
    </div>
  );
}

// The drilled-in post grid for one platform, with the sort toggle + back.
function PlatformPosts({
  platform,
  posts,
  sort,
  onSort,
  onBack,
}: {
  platform: string;
  posts: PostItem[];
  sort: SortKey;
  onSort: (s: SortKey) => void;
  onBack: () => void;
}): JSX.Element {
  return (
    <div style={{ display: 'grid', gap: 'var(--s-3)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--s-2)', flexWrap: 'wrap' }}>
        <button
          type="button"
          data-testid="gallery-back"
          onClick={onBack}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 'var(--s-1)',
            padding: '6px 10px',
            borderRadius: 'var(--r-sm)',
            border: '1px solid var(--line)',
            background: 'var(--surface-2)',
            color: 'var(--ink)',
            cursor: 'pointer',
            fontSize: 'var(--fs-sm)',
          }}
        >
          <ArrowLeft size={14} aria-hidden /> All platforms
        </button>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--s-1)' }}>
          <PlatformIcon platform={platform} />
          <strong style={{ fontSize: 'var(--fs-sm)' }}>{platformLabel(platform)}</strong>
        </span>

        {/* Sort toggle — Most valuable / Most recent (re-fetches with ?sort=). */}
        <div
          role="group"
          aria-label="Sort posts"
          style={{ marginLeft: 'auto', display: 'flex', gap: 'var(--s-1)' }}
        >
          {(
            [
              ['most_valuable', 'Most valuable', Star],
              ['most_recent', 'Most recent', Clock],
            ] as [SortKey, string, LucideIcon][]
          ).map(([key, label, Icon]) => {
            const active = sort === key;
            return (
              <button
                key={key}
                type="button"
                data-testid={`gallery-sort-${key}`}
                aria-pressed={active}
                onClick={() => onSort(key)}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 'var(--s-1)',
                  padding: '6px 10px',
                  borderRadius: 'var(--r-sm)',
                  border: '1px solid var(--line)',
                  background: active ? 'var(--flow-wash)' : 'var(--surface-2)',
                  color: active ? 'var(--flow-ink)' : 'var(--ink)',
                  fontWeight: active ? 700 : 500,
                  fontSize: 'var(--fs-sm)',
                  cursor: 'pointer',
                }}
              >
                <Icon size={13} aria-hidden /> {label}
              </button>
            );
          })}
        </div>
      </div>

      {posts.length === 0 ? (
        <p
          data-testid="gallery-platform-empty"
          style={{ fontSize: 'var(--fs-sm)', color: 'var(--muted)', margin: 0 }}
        >
          No posts on {platformLabel(platform)} yet.
        </p>
      ) : (
        <ul
          data-testid="gallery-grid"
          style={{
            listStyle: 'none',
            margin: 0,
            padding: 0,
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
            gap: 'var(--s-2)',
          }}
        >
          {posts.map((post) => (
            <PostCard key={post.id} post={post} />
          ))}
        </ul>
      )}
    </div>
  );
}

// One post card: the picture (placeholder), the caption (the words), the posted
// date, and the value badge.
function PostCard({ post }: { post: PostItem }): JSX.Element {
  const isVideo = post.asset_type === 'video';
  return (
    <li
      data-testid={`gallery-post-${post.id}`}
      style={{
        borderRadius: 'var(--r-md)',
        background: 'var(--surface-2)',
        border: '1px solid var(--line)',
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* The image placeholder (media-gen not wired yet — OUT-1). */}
      <div
        data-testid={`gallery-post-image-${post.id}`}
        aria-label={`${platformLabel(post.platform)} post image (placeholder)`}
        title={post.image_ref}
        style={{
          aspectRatio: '16 / 10',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'var(--surface-3)',
          borderBottom: '1px solid var(--line)',
          color: 'var(--muted)',
        }}
      >
        {isVideo ? <Video size={28} aria-hidden /> : <ImageIcon size={28} aria-hidden />}
      </div>

      <div style={{ padding: 'var(--s-2)', display: 'grid', gap: 'var(--s-2)' }}>
        <p
          style={{
            fontSize: 'var(--fs-sm)',
            color: 'var(--ink)',
            margin: 0,
            display: '-webkit-box',
            WebkitLineClamp: 4,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
          }}
        >
          {post.caption}
        </p>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 'var(--s-1)',
          }}
        >
          <span
            className="lab"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 'var(--s-1)',
              color: 'var(--muted)',
            }}
          >
            <PlatformIcon platform={post.platform} />
            {post.posted_at}
          </span>
          <span data-testid={`gallery-post-value-${post.id}`}>
            <Chip tone="gate">
              <Star size={11} aria-hidden style={{ marginRight: 2 }} />
              {post.value.toFixed(0)}
            </Chip>
          </span>
        </div>
      </div>
    </li>
  );
}
