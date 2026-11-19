import type { MetadataRoute } from 'next'

export default function sitemap(): MetadataRoute.Sitemap {
  const base = 'https://www.hail-cast-mendoza.com'
  return [
    { url: `${base}/`, lastModified: new Date(), changeFrequency: 'always', priority: 1 },
    { url: `${base}/acerca`, lastModified: new Date(), changeFrequency: 'monthly', priority: 0.8 },
    { url: `${base}/story`, lastModified: new Date(), changeFrequency: 'monthly', priority: 0.5 },
  ]
}
