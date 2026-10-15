import type { Metadata } from 'next'
import { Inter, IBM_Plex_Mono } from 'next/font/google'
import './globals.css'
import { Toaster } from '@/components/ui/toaster'

const inter = Inter({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-sans',
})

const plexMono = IBM_Plex_Mono({
  subsets: ['latin'],
  weight: ['400', '500', '600'],
  display: 'swap',
  variable: '--font-mono',
})

export const metadata: Metadata = {
  title: 'My App',
  description: 'Built with the FP Redwood Professional design system',
}

/**
 * Pre-hydration script: read the user's saved theme and apply the `dark`
 * class to <html> BEFORE first paint. This avoids a flash of light/dark mode
 * during hydration. Storage key matches the one used in `lib/theme.ts`.
 *
 * Inline string so it ships in the initial HTML — do not move into a file
 * or wrap in a component; both defeat the purpose.
 */
const themeInitScript = `
(function() {
  try {
    var saved = localStorage.getItem('fp_theme');
    var prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    var dark = saved === 'dark' || (saved === 'system' && prefersDark) || (!saved && false);
    if (dark) {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
  } catch (e) {}
})();
`

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" suppressHydrationWarning className={`${inter.variable} ${plexMono.variable}`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body className="font-sans antialiased min-h-screen bg-background text-foreground">
        {children}
        <Toaster />
      </body>
    </html>
  )
}
