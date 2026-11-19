import type { Metadata, Viewport } from 'next'
import localFont from 'next/font/local'
import './globals.css'
import 'leaflet/dist/leaflet.css'
import { AuthProvider } from '@/lib/auth-context'
import { ServiceWorkerRegister } from '@/components/sw-register'
import ErrorBoundary from '@/components/error-boundary'
import { PushProvider } from '@/lib/push-context'
import { LanguageProvider } from '@/lib/language-context'
import { Analytics } from "@vercel/analytics/react"
import { SpeedInsights } from "@vercel/speed-insights/next"

const montserrat = localFont({
  src: [
    {
      path: '../public/fonts/Montserrat/Montserrat-VariableFont_wght.ttf',
      style: 'normal',
    },
    {
      path: '../public/fonts/Montserrat/Montserrat-Italic-VariableFont_wght.ttf',
      style: 'italic',
    }
  ],
  variable: '--font-sans',
})

const SITE_URL = 'https://www.hail-cast-mendoza.com'
const SITE_DESCRIPTION =
  'Radar meteorológico en vivo y pronóstico a 30 minutos con inteligencia artificial para el sur de Mendoza. Alertas gratuitas de granizo y tormentas severas en tu celular.'

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: 'HailCast · Radar y alertas de granizo en Mendoza',
    template: '%s · HailCast',
  },
  description: SITE_DESCRIPTION,
  applicationName: 'HailCast',
  keywords: [
    'radar meteorológico', 'granizo', 'alerta de granizo', 'tormentas', 'Mendoza',
    'San Rafael', 'General Alvear', 'Malargüe', 'pronóstico', 'nowcasting', 'radar en vivo',
  ],
  alternates: { canonical: '/' },
  robots: { index: true, follow: true },
  openGraph: {
    type: 'website',
    url: SITE_URL,
    siteName: 'HailCast',
    locale: 'es_AR',
    title: 'HailCast · Radar y alertas de granizo en Mendoza',
    description: SITE_DESCRIPTION,
    images: [{ url: '/og.png', width: 1200, height: 630, alt: 'Radar de HailCast sobre el sur de Mendoza' }],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'HailCast · Radar y alertas de granizo en Mendoza',
    description: SITE_DESCRIPTION,
    images: ['/og.png'],
  },
  manifest: '/manifest.json',
  appleWebApp: {
    capable: true,
    statusBarStyle: 'black-translucent',
    title: 'Hailcast',
  },
}

export const viewport: Viewport = {
  themeColor: '#0f172a',
  width: 'device-width',
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
}

// Datos estructurados para Google (schema.org). Describen qué es el sitio, a quién sirve y que es gratis.
const jsonLd = {
  '@context': 'https://schema.org',
  '@graph': [
    {
      '@type': 'WebApplication',
      '@id': `${SITE_URL}/#app`,
      name: 'HailCast',
      alternateName: 'HailCast Mendoza',
      url: SITE_URL,
      description: SITE_DESCRIPTION,
      applicationCategory: 'WeatherApplication',
      operatingSystem: 'Web, Android, iOS',
      browserRequirements: 'Requires JavaScript',
      inLanguage: 'es-AR',
      isAccessibleForFree: true,
      offers: { '@type': 'Offer', price: '0', priceCurrency: 'ARS' },
      image: `${SITE_URL}/og.png`,
      screenshot: `${SITE_URL}/og.png`,
      featureList: [
        'Radar meteorológico en vivo',
        'Pronóstico de tormentas a 30 minutos con inteligencia artificial',
        'Alertas de granizo y tormentas severas por notificación push y Telegram',
        'Alertas por cercanía a tu ubicación',
        'Reportes ciudadanos de granizo, lluvia y viento',
      ],
      areaServed: [
        { '@type': 'City', name: 'San Rafael', containedInPlace: { '@type': 'State', name: 'Mendoza' } },
        { '@type': 'City', name: 'General Alvear', containedInPlace: { '@type': 'State', name: 'Mendoza' } },
        { '@type': 'City', name: 'Malargüe', containedInPlace: { '@type': 'State', name: 'Mendoza' } },
        { '@type': 'State', name: 'Mendoza', containedInPlace: { '@type': 'Country', name: 'Argentina' } },
      ],
      author: { '@id': `${SITE_URL}/#author` },
      keywords: 'radar meteorológico, granizo, alerta de granizo, tormentas, Mendoza, San Rafael, General Alvear, Malargüe, nowcasting',
    },
    {
      '@type': 'Person',
      '@id': `${SITE_URL}/#author`,
      name: 'Federico Caballero',
      email: 'mailto:federicorcaballero@gmail.com',
      sameAs: ['https://linkedin.com/in/federico-caballero1', 'https://github.com/fede-caballero'],
      affiliation: { '@type': 'CollegeOrUniversity', name: 'Universidad de Mendoza' },
    },
    {
      '@type': 'WebSite',
      '@id': `${SITE_URL}/#website`,
      url: SITE_URL,
      name: 'HailCast',
      inLanguage: 'es-AR',
      publisher: { '@id': `${SITE_URL}/#author` },
    },
  ],
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="es" translate="no" className={`${montserrat.variable}`}>
      <head>
        <link rel="icon" type="image/png" href="/favicon-32.png" sizes="32x32" />
        <link rel="shortcut icon" href="/favicon-32.png" />
        <link rel="apple-touch-icon" href="/apple-touch-icon.png" />
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
        />
      </head>
      <body className="font-sans bg-black text-white antialiased overflow-hidden overscroll-none touch-none">
        <ErrorBoundary>
          <LanguageProvider>
            <AuthProvider>
              <PushProvider>
                <ServiceWorkerRegister />
                {children}
                <Analytics />
                <SpeedInsights />
              </PushProvider>
            </AuthProvider>
          </LanguageProvider>
        </ErrorBoundary>
      </body>
    </html>
  )
}
