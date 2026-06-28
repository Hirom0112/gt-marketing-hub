import type { Metadata } from 'next';
import './globals.css';
import { SessionProvider } from '@/lib/session';
import { AppShell } from '@/components/AppShell';

export const metadata: Metadata = {
  title: 'GT Marketing Hub',
  description: 'GT Anywhere · Operations Almanac — the centralized marketing operating console.',
};

// Fonts: the GT Pulse system — Fraunces (serif display/figures), Geist (UI/body),
// JetBrains Mono (telemetry/data).
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="light">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600;9..144,700&family=Geist:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>
        <SessionProvider>
          <AppShell>{children}</AppShell>
        </SessionProvider>
      </body>
    </html>
  );
}
