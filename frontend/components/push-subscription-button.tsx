'use client'

import { Settings } from 'lucide-react'
import { Button } from "@/components/ui/button"

// Engranaje de Ajustes. Se muestra SIEMPRE: antes se ocultaba cuando el navegador
// no soportaba Web Push (Safari y Firefox en iPhone fuera de la PWA), y el usuario
// se quedaba sin acceso a idioma, capas y Telegram. El aviso de "push no soportado"
// vive dentro del diálogo de ajustes.
export function PushSubscriptionButton({ onClick }: { onClick?: () => void }) {
    return (
        <Button
            variant="ghost"
            size="icon"
            onClick={onClick}
            title={"Configuraciones"}
            className={"text-zinc-900 hover:text-black hover:bg-zinc-200/50 h-11 w-11 rounded-full"}
        >
            <Settings className="h-6 w-6 stroke-[2.5px]" />
        </Button>
    )
}
