import type { MetadataRoute } from 'next'

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{ userAgent: '*', allow: '/', disallow: ['/api/', '/auth/', '/login', '/register', '/reset-password'] }],
    sitemap: 'https://www.hail-cast-mendoza.com/sitemap.xml',
  }
}
