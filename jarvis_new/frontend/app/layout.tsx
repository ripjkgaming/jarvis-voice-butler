import localFont from 'next/font/local';
import { ThemeProvider } from '@/components/app/theme-provider';
import { cn } from '@/lib/shadcn/utils';
import { getDefaultConfig, getStyles } from '@/lib/utils';
import '@/styles/globals.css';

const commitMono = localFont({
  display: 'swap',
  variable: '--font-commit-mono',
  src: [
    {
      path: '../fonts/CommitMono-400-Regular.otf',
      weight: '400',
      style: 'normal',
    },
    {
      path: '../fonts/CommitMono-700-Regular.otf',
      weight: '700',
      style: 'normal',
    },
    {
      path: '../fonts/CommitMono-400-Italic.otf',
      weight: '400',
      style: 'italic',
    },
    {
      path: '../fonts/CommitMono-700-Italic.otf',
      weight: '700',
      style: 'italic',
    },
  ],
});

// STARK OS type (design/STARK_OS.md): Rajdhani labels, Orbitron wordmark,
// Share Tech Mono read-outs. Vendored, so the offline shell never fetches.
const rajdhani = localFont({
  display: 'swap',
  variable: '--font-rajdhani',
  src: [
    { path: '../fonts/Rajdhani-Regular.ttf', weight: '400', style: 'normal' },
    { path: '../fonts/Rajdhani-Medium.ttf', weight: '500', style: 'normal' },
    { path: '../fonts/Rajdhani-SemiBold.ttf', weight: '600', style: 'normal' },
    { path: '../fonts/Rajdhani-Bold.ttf', weight: '700', style: 'normal' },
  ],
});

const orbitron = localFont({
  display: 'swap',
  variable: '--font-orbitron',
  src: [{ path: '../fonts/Orbitron-Variable.ttf', weight: '400 900', style: 'normal' }],
});

const shareTechMono = localFont({
  display: 'swap',
  variable: '--font-share-tech-mono',
  src: [{ path: '../fonts/ShareTechMono-Regular.ttf', weight: '400', style: 'normal' }],
});

interface RootLayoutProps {
  children: React.ReactNode;
}

export default function RootLayout({ children }: RootLayoutProps) {
  const appConfig = getDefaultConfig();
  const styles = getStyles(appConfig);
  const { pageTitle, pageDescription } = appConfig;

  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={cn(
        commitMono.variable,
        rajdhani.variable,
        orbitron.variable,
        shareTechMono.variable,
        'scroll-smooth font-sans antialiased'
      )}
    >
      <head>
        {styles && <style>{styles}</style>}
        <title>{pageTitle}</title>
        <meta name="description" content={pageDescription} />
      </head>
      <body className="overflow-x-hidden">
        <ThemeProvider
          attribute="class"
          defaultTheme="dark"
          forcedTheme="dark"
          disableTransitionOnChange
        >
          {children}
        </ThemeProvider>
      </body>
    </html>
  );
}
