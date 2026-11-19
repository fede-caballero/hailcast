import type { Metadata } from 'next'
import Link from 'next/link'

// Página estática con texto indexable: es lo único del sitio que Google puede leer como contenido.
// El mapa principal no tiene texto, así que acá van las palabras por las que queremos aparecer.

export const metadata: Metadata = {
  title: { absolute: 'Acerca de HailCast · Radar y alertas de granizo en San Rafael, General Alvear y Malargüe' },
  description:
    'HailCast es un radar meteorológico en vivo con pronóstico a 30 minutos por inteligencia artificial y alertas gratuitas de granizo para el sur de Mendoza: San Rafael, General Alvear y Malargüe.',
  alternates: { canonical: '/acerca' },
  openGraph: {
    title: 'Acerca de HailCast',
    description:
      'Radar meteorológico en vivo, pronóstico a 30 minutos con IA y alertas gratuitas de granizo para el sur de Mendoza.',
    url: 'https://www.hail-cast-mendoza.com/acerca',
  },
}

const faq = [
  {
    q: '¿Qué es HailCast?',
    a: 'Es una aplicación web gratuita que muestra el radar meteorológico del sur de Mendoza en tiempo real y pronostica hacia dónde se mueven las tormentas en los próximos 30 minutos usando inteligencia artificial. Funciona en el celular como una app, sin instalar nada.',
  },
  {
    q: '¿Qué zona cubre?',
    a: 'El radar está en San Rafael y cubre los departamentos de San Rafael, General Alvear y Malargüe, en el sur de la provincia de Mendoza, Argentina. Es la zona vitivinícola y frutícola más castigada por el granizo del país.',
  },
  {
    q: '¿Cómo funcionan las alertas de granizo?',
    a: 'Cuando el radar detecta una celda de tormenta severa, con reflectividad de más de 50 dBZ a una altura donde solo hay convección fuerte, el sistema manda una notificación al celular. Si activás tu ubicación, te avisa cuando una tormenta se acerca a menos de 20 km de donde estás. También podés recibir las alertas por Telegram.',
  },
  {
    q: '¿Cómo activo las alertas en mi celular?',
    a: 'Entrá a la página desde el celular, tocá el botón de la campana y aceptá las notificaciones. En iPhone primero hay que agregar la página a la pantalla de inicio desde el menú de compartir de Safari. No hace falta crear una cuenta.',
  },
  {
    q: '¿Cómo pronostica el movimiento de las tormentas?',
    a: 'Un modelo de aprendizaje profundo del tipo ConvLSTM, entrenado con miles de secuencias históricas del mismo radar, recibe los últimos barridos y genera los cuadros siguientes. El resultado se muestra en el mapa como pronóstico, con una línea de tiempo que va de lo observado a lo previsto.',
  },
  {
    q: '¿Qué significan los colores del radar?',
    a: 'Los colores indican la intensidad de la precipitación en dBZ. Verde y azul es lluvia débil. Amarillo y naranja es lluvia fuerte. Rojo y magenta, por encima de 50 dBZ, indica lluvia torrencial con probable granizo.',
  },
  {
    q: '¿Puedo reportar granizo o lluvia en mi zona?',
    a: 'Sí. Con una cuenta podés mandar un reporte con tu ubicación, el tipo de fenómeno y una foto. Los reportes aparecen en el mapa para todos y ayudan a validar lo que ve el radar.',
  },
  {
    q: '¿Quién desarrolla HailCast?',
    a: 'Federico Caballero, como proyecto final de grado en la Universidad de Mendoza. La aplicación se usa de forma operativa durante la campaña de tormentas y se sigue mejorando con lo que reportan los usuarios.',
  },
]

const faqJsonLd = {
  '@context': 'https://schema.org',
  '@type': 'FAQPage',
  mainEntity: faq.map(({ q, a }) => ({
    '@type': 'Question',
    name: q,
    acceptedAnswer: { '@type': 'Answer', text: a },
  })),
}

export default function AcercaPage() {
  return (
    <div className="fixed inset-0 overflow-y-auto touch-auto bg-zinc-950 text-zinc-100 font-sans">
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(faqJsonLd) }} />

      <header className="sticky top-0 z-10 bg-zinc-950/90 backdrop-blur border-b border-white/10">
        <div className="max-w-3xl mx-auto px-5 py-3 flex items-center justify-between">
          <Link href="/" className="flex items-center gap-2 font-bold tracking-wide">
            <img src="/logo.png" alt="Logo de HailCast" className="w-8 h-8 object-contain" />
            HailCast
          </Link>
          <Link
            href="/"
            className="text-sm font-semibold bg-orange-400 text-black hover:bg-orange-500 rounded-md px-3 py-1.5"
          >
            Ver el radar en vivo
          </Link>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-5 py-10 space-y-12">
        <section className="space-y-4">
          <h1 className="text-3xl sm:text-4xl font-black leading-tight">
            Radar meteorológico y alertas de granizo para el sur de Mendoza
          </h1>
          <p className="text-lg text-zinc-300">
            HailCast muestra el radar de San Rafael en vivo, pronostica el movimiento de las tormentas a 30 minutos
            con inteligencia artificial y manda alertas gratuitas de granizo al celular. Cubre San Rafael, General
            Alvear y Malargüe.
          </p>
          <p className="text-zinc-400">
            Pensado para productores, bodegas, parques solares, aseguradoras y cualquier vecino que quiera saber si
            la tormenta que se ve en el horizonte viene para su lado.
          </p>
          <div className="flex flex-wrap gap-3 pt-2">
            <Link href="/" className="bg-blue-600 hover:bg-blue-500 text-white font-semibold rounded-md px-4 py-2">
              Abrir el mapa
            </Link>
            <Link href="/story" className="border border-white/20 hover:bg-white/10 rounded-md px-4 py-2">
              Ver una tormenta histórica
            </Link>
          </div>
        </section>

        <section className="grid sm:grid-cols-3 gap-4">
          {[
            ['Radar en vivo', 'Un barrido nuevo cada pocos minutos, con celdas de tormenta identificadas y su dirección de desplazamiento.'],
            ['Pronóstico a 30 minutos', 'Un modelo ConvLSTM entrenado con años de datos del mismo radar anticipa hacia dónde van las tormentas.'],
            ['Alertas gratis', 'Notificaciones en el celular y por Telegram cuando hay granizo cerca de tu ubicación o de tu campo.'],
          ].map(([title, body]) => (
            <div key={title} className="rounded-xl border border-white/10 bg-white/5 p-4 space-y-1">
              <h2 className="font-bold">{title}</h2>
              <p className="text-sm text-zinc-400">{body}</p>
            </div>
          ))}
        </section>

        <section className="space-y-6">
          <h2 className="text-2xl font-bold">Preguntas frecuentes</h2>
          {faq.map(({ q, a }) => (
            <div key={q} className="space-y-1">
              <h3 className="font-semibold text-lg">{q}</h3>
              <p className="text-zinc-400">{a}</p>
            </div>
          ))}
        </section>

        <section className="space-y-3 rounded-xl border border-white/10 bg-white/5 p-5">
          <h2 className="text-xl font-bold">Contacto</h2>
          <p className="text-zinc-400">
            ¿Querés usar HailCast en tu empresa, cooperativa o municipio, o integrar las alertas con tu sistema?
            Escribime.
          </p>
          <ul className="space-y-1 text-sm">
            <li>
              Email:{' '}
              <a className="text-sky-400 hover:underline" href="mailto:federicorcaballero@gmail.com">
                federicorcaballero@gmail.com
              </a>
            </li>
            <li>
              LinkedIn:{' '}
              <a className="text-sky-400 hover:underline" href="https://linkedin.com/in/federico-caballero1" target="_blank" rel="noopener noreferrer">
                federico-caballero1
              </a>
            </li>
            <li>
              GitHub:{' '}
              <a className="text-sky-400 hover:underline" href="https://github.com/fede-caballero" target="_blank" rel="noopener noreferrer">
                fede-caballero
              </a>
            </li>
          </ul>
        </section>

        <footer className="text-xs text-zinc-500 pb-8">
          HailCast · Proyecto final de grado, Universidad de Mendoza · San Rafael, Mendoza, Argentina
        </footer>
      </main>
    </div>
  )
}
