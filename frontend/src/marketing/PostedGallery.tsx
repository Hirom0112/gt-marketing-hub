import { useEffect, useState } from 'react';
import {
  ArrowLeft,
  AtSign,
  Clock,
  ExternalLink,
  Globe,
  Hash,
  Heart,
  Image as ImageIcon,
  MessageCircle,
  Play,
  Share2,
  Star,
  Video,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { apiBaseUrl, apiFetch } from '../config';
import { Card, Chip } from '../ui';

// Posted-content gallery (FR-3.4) — "see every picture we ever posted and WHERE,
// and the WORDS along with it." The kept+validated library posts grouped by the
// platform they came FROM (GET /content/gallery). The flow is:
//   platform tiles (with per-platform counts)
//     → click a tile → GET /content/gallery?platform=<p> → a grid of post cards
//        (real media + caption + engagement badge + posted date + value badge +
//         a "View original" link)
//     → a Most valuable / Most recent sort toggle (re-fetches with ?sort=)
//     → a back-to-all affordance returns to the tiles.
//
// When the backend has GT_POSTED_CATALOG_ROOT configured the cards render the REAL
// posted media (served at /posted-media, resolved against apiBaseUrl), real captions,
// real engagement, and engagement-based ranking (the scoped INV-1 exception). With no
// catalog the backend falls back to the synthetic library gallery (image_ref is a
// placeholder ref, no engagement) and the card degrades gracefully.
// Read-only (the deterministic core owns all writes, INV-2); native fetch only.

// One platform tile: the platform + how many posts it holds.
interface PlatformGroup {
  platform: string;
  count: number;
}

// One post card: the picture/video, the words, where + when + engagement + value.
// The engagement fields + url are present on the REAL-catalog path; on the library
// fallback they are absent (the badge omits them gracefully).
interface PostItem {
  id: string;
  platform: string;
  asset_type: string;
  caption: string;
  image_ref: string;
  posted_at: string;
  value: number;
  likes?: number | null;
  views?: number | null;
  comments?: number | null;
  url?: string | null;
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
    apiFetch(`/content/gallery?${qs.toString()}`)
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
          Every post we made · by platform, ranked
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

        {/* Sort toggle · Most valuable / Most recent (re-fetches with ?sort=). */}
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

// Resolve a served media_ref ("/posted-media/...") against the API base so the
// browser fetches it from the backend that mounted the scrape root. A non-served
// ref (e.g. a library "placeholder://" fallback) passes through unchanged.
function mediaSrc(ref: string): string {
  return ref.startsWith('/') ? `${apiBaseUrl}${ref}` : ref;
}

// The engagement badge — ❤ likes · ▶ views · 💬 comments. Each metric is omitted
// when its count is zero/absent, so the badge degrades gracefully (and the library
// fallback, which carries no counts, shows nothing).
function EngagementBadge({ post }: { post: PostItem }): JSX.Element | null {
  const metrics: [LucideIcon, number][] = [];
  if (post.likes) metrics.push([Heart, post.likes]);
  if (post.views) metrics.push([Play, post.views]);
  if (post.comments) metrics.push([MessageCircle, post.comments]);
  return (
    <span
      data-testid={`gallery-post-engagement-${post.id}`}
      className="lab"
      style={{ display: 'inline-flex', alignItems: 'center', gap: 'var(--s-2)', color: 'var(--muted)' }}
    >
      {metrics.length === 0 ? (
        <span style={{ opacity: 0.6 }}>No engagement yet</span>
      ) : (
        metrics.map(([Icon, count], i) => (
          <span
            key={i}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 2 }}
          >
            <Icon size={12} aria-hidden /> {count.toLocaleString()}
          </span>
        ))
      )}
    </span>
  );
}

// One post card: the REAL media (img or video), the caption (the words), the posted
// date, the engagement badge, a "View original" link, and the value badge.
function PostCard({ post }: { post: PostItem }): JSX.Element {
  const isVideo = post.asset_type === 'video';
  const src = mediaSrc(post.image_ref);
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
      {/* The real posted media · a <video controls> for video, else an <img>. */}
      {isVideo ? (
        <video
          data-testid={`gallery-post-media-${post.id}`}
          src={src}
          controls
          preload="metadata"
          aria-label={`${platformLabel(post.platform)} post video`}
          style={{
            width: '100%',
            aspectRatio: '16 / 10',
            objectFit: 'cover',
            background: 'var(--surface-3)',
            borderBottom: '1px solid var(--line)',
          }}
        />
      ) : (
        <img
          data-testid={`gallery-post-media-${post.id}`}
          src={src}
          alt={`${platformLabel(post.platform)} post`}
          loading="lazy"
          style={{
            width: '100%',
            aspectRatio: '16 / 10',
            objectFit: 'cover',
            background: 'var(--surface-3)',
            borderBottom: '1px solid var(--line)',
          }}
        />
      )}

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

        <EngagementBadge post={post} />

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

        {post.url ? (
          <a
            data-testid={`gallery-post-link-${post.id}`}
            href={post.url}
            target="_blank"
            rel="noopener noreferrer"
            className="lab"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 'var(--s-1)',
              color: 'var(--flow-ink)',
              textDecoration: 'none',
            }}
          >
            <ExternalLink size={12} aria-hidden /> View original
          </a>
        ) : null}
      </div>
    </li>
  );
}
