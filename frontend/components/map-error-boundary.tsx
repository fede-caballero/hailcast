'use client'

import React, { Component, ErrorInfo, ReactNode } from 'react'
import { RefreshCw } from 'lucide-react'

interface Props {
    children?: ReactNode
}

interface State {
    hasError: boolean
    error: Error | null
    attempt: number
}

/**
 * Límite de error exclusivo del mapa. En móviles el navegador puede descartar el
 * contexto WebGL (app en segundo plano, poca memoria) y MapLibre deja `map.style`
 * en null un instante; cualquier acceso al mapa en ese momento explota. Antes eso
 * tiraba toda la app al ErrorBoundary global; acá se ofrece remontar solo el mapa.
 */
export class MapErrorBoundary extends Component<Props, State> {
    public state: State = { hasError: false, error: null, attempt: 0 }

    public static getDerivedStateFromError(error: Error): Partial<State> {
        return { hasError: true, error }
    }

    public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
        console.error('[MapErrorBoundary] Error en el mapa:', error, errorInfo)
    }

    private retry = () => {
        this.setState((s) => ({ hasError: false, error: null, attempt: s.attempt + 1 }))
    }

    public render() {
        if (this.state.hasError) {
            return (
                <div className="w-full h-full bg-slate-900 flex items-center justify-center p-6">
                    <div className="bg-zinc-900/90 border border-white/10 rounded-xl p-5 max-w-sm w-full text-center shadow-2xl">
                        <p className="text-white font-semibold mb-1">El mapa dejó de responder</p>
                        <p className="text-zinc-400 text-xs mb-4">
                            Suele pasar al volver a la app en el celular. Tocá reintentar; si persiste, recargá la página.
                        </p>
                        <button
                            onClick={this.retry}
                            className="inline-flex items-center gap-2 bg-white text-black font-bold text-sm px-4 py-2 rounded-lg hover:bg-zinc-200 transition-colors"
                        >
                            <RefreshCw className="h-4 w-4" />
                            Reintentar
                        </button>
                        <p className="text-zinc-600 font-mono text-[10px] mt-4 break-all">{this.state.error?.message}</p>
                    </div>
                </div>
            )
        }
        // La key fuerza el remontaje completo del mapa en cada reintento
        return <React.Fragment key={this.state.attempt}>{this.props.children}</React.Fragment>
    }
}
