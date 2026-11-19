'use client'

import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { Switch } from "@/components/ui/switch"
import { usePush } from "@/lib/push-context"
import { useLanguage } from "@/lib/language-context"
import { useAuth } from "@/lib/auth-context"
import { Bell, AlertTriangle, Plane, Globe, Settings as SettingsIcon, BellRing, Cloud, MessageSquare, Thermometer, Layers, Send, Zap } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useToast } from "@/components/ui/use-toast"
import { useCallback, useEffect, useRef, useState } from "react"
import { createTelegramLink, fetchTelegramStatus, setTelegramEnabled, unlinkTelegram, TelegramStatus } from "@/lib/api"

interface SettingsDialogProps {
    open: boolean
    onOpenChange: (open: boolean) => void
    showStormCells?: boolean
    onShowStormCellsChange?: (show: boolean) => void
    showLightning?: boolean
    onShowLightningChange?: (show: boolean) => void
    showReports?: boolean
    onShowReportsChange?: (show: boolean) => void
    showWeatherWidget?: boolean
    onShowWeatherWidgetChange?: (show: boolean) => void
    showStations?: boolean
    onShowStationsChange?: (show: boolean) => void
    showSmoothRadar?: boolean
    onShowSmoothRadarChange?: (show: boolean) => void
}

export function SettingsDialog({
    open,
    onOpenChange,
    showStormCells = true,
    onShowStormCellsChange,
    showLightning = true,
    onShowLightningChange,
    showReports = true,
    onShowReportsChange,
    showWeatherWidget = true,
    onShowWeatherWidgetChange,
    showStations = true,
    onShowStationsChange,
    showSmoothRadar = false,
    onShowSmoothRadarChange
}: SettingsDialogProps) {
    const { isSubscribed, preferences, updatePreferences, isSupported, subscribe, unsubscribe, loading } = usePush()
    const { language, setLanguage, t } = useLanguage()
    const { toast } = useToast()
    const { token } = useAuth()

    // --- Telegram: vinculación de cuenta ---
    const [telegram, setTelegram] = useState<TelegramStatus | null>(null)
    const [telegramBusy, setTelegramBusy] = useState(false)
    const [telegramLinkUrl, setTelegramLinkUrl] = useState<string | null>(null)
    const telegramPollRef = useRef<ReturnType<typeof setInterval> | null>(null)

    const stopTelegramPoll = () => {
        if (telegramPollRef.current) clearInterval(telegramPollRef.current)
        telegramPollRef.current = null
    }

    const refreshTelegram = useCallback(async () => {
        if (!token) { setTelegram(null); return null }
        const status = await fetchTelegramStatus(token)
        setTelegram(status)
        return status
    }, [token])

    useEffect(() => {
        if (open) refreshTelegram()
        else { stopTelegramPoll(); setTelegramLinkUrl(null); setTelegramBusy(false) }
    }, [open, refreshTelegram])

    useEffect(() => () => stopTelegramPoll(), [])

    const handleTelegramLink = async () => {
        if (!token) return
        try {
            setTelegramBusy(true)
            const { url } = await createTelegramLink(token)
            setTelegramLinkUrl(url)
            // El worker confirma el /start del bot; se consulta el estado hasta 3 minutos.
            let tries = 0
            stopTelegramPoll()
            telegramPollRef.current = setInterval(async () => {
                tries += 1
                const status = await refreshTelegram()
                if ((status && status.linked) || tries > 60) {
                    stopTelegramPoll()
                    setTelegramBusy(false)
                    setTelegramLinkUrl(null)
                    if (status && status.linked) {
                        toast({
                            title: t("Telegram vinculado", "Telegram linked"),
                            description: t("Ya vas a recibir las alertas también por Telegram.", "You will now also receive alerts on Telegram."),
                        })
                    }
                }
            }, 3000)
        } catch (error: any) {
            setTelegramBusy(false)
            toast({ variant: "destructive", title: "Telegram", description: error.message || t("No se pudo generar el link.", "Could not create the link.") })
        }
    }

    const handleTelegramToggle = async (enabled: boolean) => {
        if (!token) return
        setTelegramBusy(true)
        try {
            setTelegram(await setTelegramEnabled(token, enabled))
        } catch (error: any) {
            toast({ variant: "destructive", title: "Telegram", description: error.message })
        } finally {
            setTelegramBusy(false)
        }
    }

    const handleTelegramUnlink = async () => {
        if (!token) return
        setTelegramBusy(true)
        try {
            setTelegram(await unlinkTelegram(token))
            toast({ title: t("Cuenta desvinculada", "Account unlinked"), description: t("Ya no vas a recibir alertas por Telegram.", "You will no longer receive alerts on Telegram.") })
        } catch (error: any) {
            toast({ variant: "destructive", title: "Telegram", description: error.message })
        } finally {
            setTelegramBusy(false)
        }
    }

    const handleMasterToggle = async (checked: boolean) => {
        try {
            if (checked) {
                const result = await subscribe()
                if (result) {
                    toast({
                        title: t("Notificaciones Activas", "Notifications Enabled"),
                        description: t("Recibirás alertas de tormentas severas.", "You will receive severe storm alerts."),
                    })
                } else {
                    toast({
                        variant: "destructive",
                        title: t("Error", "Error"),
                        description: t("No se pudo activar. Verifica permisos.", "Could not enable. Check permissions."),
                    })
                }
            } else {
                await unsubscribe()
                toast({
                    title: t("Notificaciones Desactivadas", "Notifications Disabled"),
                    description: t("Ya no recibirás alertas en este dispositivo.", "You will no longer receive alerts on this device."),
                })
            }
        } catch (error: any) {
            console.error(error)
            toast({
                variant: "destructive",
                title: t("Error de Suscripción", "Subscription Error"),
                description: error.message || t("Ocurrió un problema desconocido.", "An unknown issue occurred."),
            })
        }
    }

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-md bg-[#1c1c1e] text-white border-none shadow-2xl rounded-xl">
                <DialogHeader className="pt-2">
                    <DialogTitle className="text-xl font-bold flex items-center gap-2">
                        <SettingsIcon className="h-5 w-5 text-gray-400" />
                        {t('Configuración', 'Settings')}
                    </DialogTitle>
                    <DialogDescription className="text-gray-400">
                        {t('Personalizá la aplicación y tus alertas.', 'Customize the application and your alerts.')}
                    </DialogDescription>
                </DialogHeader>

                <div className="space-y-8 py-2">
                    {/* Language Settings */}
                    <div className="space-y-4">
                        <h4 className="text-xs font-bold tracking-wider text-gray-500 uppercase flex items-center gap-2">
                            <Globe className="h-4 w-4" />
                            {t('Idioma', 'Language')}
                        </h4>
                        <div className="grid grid-cols-2 gap-3">
                            <Button
                                variant={language === 'es' ? 'default' : 'outline'}
                                className={`transition-all ${language === 'es' ? 'bg-sky-500 hover:bg-sky-600 border-sky-500 shadow-md shadow-sky-500/20' : 'bg-[#2c2c2e] hover:bg-[#3c3c3e] border-transparent text-gray-300'}`}
                                onClick={() => setLanguage('es')}
                            >
                                Español (ES)
                            </Button>
                            <Button
                                variant={language === 'en' ? 'default' : 'outline'}
                                className={`transition-all ${language === 'en' ? 'bg-sky-500 hover:bg-sky-600 border-sky-500 shadow-md shadow-sky-500/20' : 'bg-[#2c2c2e] hover:bg-[#3c3c3e] border-transparent text-gray-300'}`}
                                onClick={() => setLanguage('en')}
                            >
                                English (US)
                            </Button>
                        </div>
                    </div>

                    {/* Map Visualization Settings */}
                    <div className="space-y-4">
                        <h4 className="text-xs font-bold tracking-wider text-gray-500 uppercase flex items-center gap-2">
                            <Globe className="h-4 w-4" />
                            {t('Visualización del Mapa', 'Map Visualization')}
                        </h4>

                        <div className="space-y-5 bg-[#2c2c2e] p-4 rounded-xl border border-white/5 shadow-sm">
                            <div className="flex items-center justify-between">
                                <div className="space-y-1 max-w-[80%] pr-4">
                                    <div className="flex items-center gap-2">
                                        <AlertTriangle className="h-4 w-4 text-orange-400" />
                                        <span className="font-semibold text-sm">
                                            {t('Celdas de Tormenta', 'Storm Cells')}
                                        </span>
                                    </div>
                                    <p className="text-xs text-gray-400 leading-snug">
                                        {t('Mostrar marcadores en los núcleos de tormentas intensas.', 'Show markers on intense storm cores.')}
                                    </p>
                                </div>
                                <Switch
                                    checked={showStormCells}
                                    onCheckedChange={(c) => onShowStormCellsChange && onShowStormCellsChange(c)}
                                    className="data-[state=checked]:bg-sky-500"
                                />
                            </div>

                            <div className="h-[1px] w-full bg-[#3c3c3e]" />

                            {/* Rayos GLM */}
                            <div className="flex items-center justify-between">
                                <div className="space-y-1 max-w-[80%] pr-4">
                                    <div className="flex items-center gap-2">
                                        <Zap className="h-4 w-4 text-yellow-400" />
                                        <span className="font-semibold text-sm">
                                            {t('Rayos en tiempo real', 'Real-time Lightning')}
                                        </span>
                                    </div>
                                    <p className="text-xs text-gray-400 leading-snug">
                                        {t('Descargas de los últimos 10 minutos detectadas por el satélite GOES-19 (GLM). Amarillo: recientes; gris: más viejas.', 'Flashes from the last 10 minutes detected by the GOES-19 satellite (GLM). Yellow: recent; grey: older.')}
                                    </p>
                                </div>
                                <Switch
                                    checked={showLightning}
                                    onCheckedChange={(c) => onShowLightningChange && onShowLightningChange(c)}
                                    className="data-[state=checked]:bg-sky-500"
                                />
                            </div>

                            <div className="h-[1px] w-full bg-[#3c3c3e]" />

                            <div className="flex items-center justify-between">
                                <div className="space-y-1 max-w-[80%] pr-4">
                                    <div className="flex items-center gap-2">
                                        <MessageSquare className="h-4 w-4 text-blue-400" />
                                        <span className="font-semibold text-sm">
                                            {t('Reportes de Usuarios', 'User Reports')}
                                        </span>
                                    </div>
                                    <p className="text-xs text-gray-400 leading-snug">
                                        {t('Ver reportes meteorológicos enviados por otros usuarios.', 'See weather reports sent by other users.')}
                                    </p>
                                </div>
                                <Switch
                                    checked={showReports}
                                    onCheckedChange={(c) => onShowReportsChange && onShowReportsChange(c)}
                                    className="data-[state=checked]:bg-sky-500"
                                />
                            </div>

                            <div className="h-[1px] w-full bg-[#3c3c3e]" />

                            <div className="flex items-center justify-between">
                                <div className="space-y-1 max-w-[80%] pr-4">
                                    <div className="flex items-center gap-2">
                                        <Cloud className="h-4 w-4 text-slate-300" />
                                        <span className="font-semibold text-sm">
                                            {t('Widget de Pronóstico', 'Weather Widget')}
                                        </span>
                                    </div>
                                    <p className="text-xs text-gray-400 leading-snug">
                                        {t('Mostrar la barra lateral de pronóstico de tu zona.', 'Show the local weather forecast sidebar.')}
                                    </p>
                                </div>
                                <Switch
                                    checked={showWeatherWidget}
                                    onCheckedChange={(c) => onShowWeatherWidgetChange && onShowWeatherWidgetChange(c)}
                                    className="data-[state=checked]:bg-sky-500"
                                />
                            </div>

                            <div className="h-[1px] w-full bg-[#3c3c3e]" />

                            <div className="flex items-center justify-between">
                                <div className="space-y-1 max-w-[80%] pr-4">
                                    <div className="flex items-center gap-2">
                                        <Thermometer className="h-4 w-4 text-orange-300" />
                                        <span className="font-semibold text-sm">
                                            {t('Estaciones Meteorológicas', 'Weather Stations')}
                                        </span>
                                    </div>
                                    <p className="text-xs text-gray-400 leading-snug">
                                        {t('Mostrar marcadores con datos en tiempo real (temperatura, humedad).', 'Show markers with real-time data (temperature, humidity).')}
                                    </p>
                                </div>
                                <Switch
                                    checked={showStations}
                                    onCheckedChange={(c) => onShowStationsChange && onShowStationsChange(c)}
                                    className="data-[state=checked]:bg-sky-500"
                                />
                            </div>

                            <div className="h-[1px] w-full bg-[#3c3c3e]" />

                            <div className="flex items-center justify-between">
                                <div className="space-y-1 max-w-[80%] pr-4">
                                    <div className="flex items-center gap-2">
                                        <Layers className="h-4 w-4 text-purple-400" />
                                        <span className="font-semibold text-sm">
                                            {t('Suavizado de Radar', 'Radar Smoothing')}
                                        </span>
                                    </div>
                                    <p className="text-xs text-gray-400 leading-snug">
                                        {t('Mostrar las celdas de tormenta con contornos difuminados para una vista más estética.', 'Show storm cells with blurred contours for a more aesthetic look.')}
                                    </p>
                                </div>
                                <Switch
                                    checked={showSmoothRadar}
                                    onCheckedChange={(c) => onShowSmoothRadarChange && onShowSmoothRadarChange(c)}
                                    className="data-[state=checked]:bg-sky-500"
                                />
                            </div>
                        </div>
                    </div>

                    {/* Push Notifications Settings */}
                    <div className="space-y-4">
                        <h4 className="text-xs font-bold tracking-wider text-gray-500 uppercase flex items-center gap-2">
                            <Bell className="h-4 w-4" />
                            {t('Notificaciones', 'Notifications')}
                        </h4>

                        {!isSupported ? (
                            <p className="text-sm text-yellow-500 bg-yellow-500/10 p-3 rounded-lg border border-yellow-500/20">
                                {t('Este navegador no soporta notificaciones web.', 'This browser does not support web push notifications.')}
                                {' '}
                                {t('En iPhone, agregá la app a la pantalla de inicio (Compartir → "Agregar a inicio") y abrila desde ahí para activarlas. Las alertas por Telegram funcionan igual.',
                                   'On iPhone, add the app to your Home Screen (Share → "Add to Home Screen") and open it from there to enable them. Telegram alerts work regardless.')}
                            </p>
                        ) : (
                            <div className="flex items-center justify-between bg-[#2c2c2e] p-4 rounded-xl border border-white/5 shadow-sm">
                                <div className="space-y-1 max-w-[80%] pr-4">
                                    <div className="flex items-center gap-2">
                                        <BellRing className={`h-4 w-4 ${isSubscribed ? 'text-green-400' : 'text-zinc-500'}`} />
                                        <span className="font-semibold text-sm">
                                            {t('Recibir Notificaciones', 'Receive Notifications')}
                                        </span>
                                    </div>
                                    <p className="text-xs text-gray-400 leading-snug">
                                        {t('Activar o desactivar todas las alertas en este dispositivo.', 'Enable or disable all alerts on this device.')}
                                    </p>
                                </div>
                                <Switch
                                    checked={isSubscribed}
                                    disabled={loading}
                                    onCheckedChange={handleMasterToggle}
                                    className="data-[state=checked]:bg-green-500"
                                />
                            </div>
                        )}

                        <div className={`space-y-5 bg-[#2c2c2e] p-4 rounded-xl ${!isSubscribed ? 'opacity-50 pointer-events-none grayscale-[0.5]' : ''}`}>
                            {/* Alertas Manuales */}
                            <div className="flex items-center justify-between">
                                <div className="space-y-1 max-w-[80%] pr-4">
                                    <div className="flex items-center gap-2">
                                        <Bell className="h-4 w-4 text-sky-400" />
                                        <span className="font-semibold text-sm">
                                            {t('Avisos Oficiales', 'Official Alerts')}
                                        </span>
                                    </div>
                                    <p className="text-xs text-gray-400 leading-snug">
                                        {t('Avisos de administradores sobre situaciones meteorológicas especiales.', 'Alerts sent by administrators during special weather situations.')}
                                    </p>
                                </div>
                                <Switch
                                    checked={preferences.alert_admin}
                                    onCheckedChange={(c) => updatePreferences({ alert_admin: c })}
                                    className="data-[state=checked]:bg-sky-500"
                                />
                            </div>

                            <div className="h-[1px] w-full bg-[#3c3c3e]" />

                            {/* Alertas de Proximidad */}
                            <div className="flex items-center justify-between">
                                <div className="space-y-1 max-w-[80%] pr-4">
                                    <div className="flex items-center gap-2">
                                        <AlertTriangle className="h-4 w-4 text-rose-500" />
                                        <span className="font-semibold text-sm">
                                            {t('Alertas de Proximidad', 'Proximity Alerts')}
                                        </span>
                                    </div>
                                    <p className="text-xs text-gray-400 leading-snug">
                                        {t('Avisos automáticos cuando hay tormenta a menos de 20km.', 'Automatic warnings when severe storms approach within 20km.')}
                                    </p>
                                </div>
                                <Switch
                                    checked={preferences.alert_proximity}
                                    onCheckedChange={(c) => updatePreferences({ alert_proximity: c })}
                                    className="data-[state=checked]:bg-sky-500"
                                />
                            </div>

                            <div className="h-[1px] w-full bg-[#3c3c3e]" />

                            {/* Despegue de Aviones */}
                            <div className="flex items-center justify-between">
                                <div className="space-y-1 max-w-[80%] pr-4">
                                    <div className="flex items-center gap-2">
                                        <Plane className="h-4 w-4 text-emerald-400" />
                                        <span className="font-semibold text-sm">
                                            {t('Despegue de Aviones', 'Aircraft Takeoffs')}
                                        </span>
                                    </div>
                                    <p className="text-xs text-gray-400 leading-snug">
                                        {t('Recibí confirmación cuando aviones de mitigación entran al radar.', 'Get notified when hail mitigation aircraft start operating.')}
                                    </p>
                                </div>
                                <Switch
                                    checked={preferences.alert_aircraft}
                                    onCheckedChange={(c) => updatePreferences({ alert_aircraft: c })}
                                    className="data-[state=checked]:bg-sky-500"
                                />
                            </div>

                            <div className="h-[1px] w-full bg-[#3c3c3e]" />

                            {/* Pronóstico Diario */}
                            <div className="flex items-center justify-between">
                                <div className="space-y-1 max-w-[80%] pr-4">
                                    <div className="flex items-center gap-2">
                                        <Cloud className="h-4 w-4 text-sky-300" />
                                        <span className="font-semibold text-sm">
                                            {t('Pronóstico Diario', 'Daily Forecast')}
                                        </span>
                                    </div>
                                    <p className="text-xs text-gray-400 leading-snug">
                                        {t('Recibí el pronóstico oficial de contingencias todos los días a las 12:00.', 'Receive the official daily weather forecast everyday at 12:00.')}
                                    </p>
                                </div>
                                <Switch
                                    checked={preferences.alert_forecast !== undefined ? preferences.alert_forecast : true}
                                    onCheckedChange={(c) => updatePreferences({ alert_forecast: c })}
                                    className="data-[state=checked]:bg-sky-500"
                                />
                            </div>
                        </div>
                    </div>

                    {/* Telegram: solo con sesión iniciada y bot configurado en el servidor */}
                    {token && telegram && telegram.configured && (
                        <div className="space-y-4">
                            <h4 className="text-xs font-bold tracking-wider text-gray-500 uppercase flex items-center gap-2">
                                <Send className="h-4 w-4" />
                                Telegram
                            </h4>

                            {!telegram.linked ? (
                                <div className="bg-[#2c2c2e] p-4 rounded-xl border border-white/5 shadow-sm space-y-3">
                                    <p className="text-xs text-gray-400 leading-snug">
                                        {t('Recibí las mismas alertas también por Telegram, aunque la app esté cerrada.', 'Get the same alerts on Telegram too, even with the app closed.')}
                                        {telegram.bot_username ? ` (@${telegram.bot_username})` : ''}
                                    </p>
                                    {telegramLinkUrl ? (
                                        <>
                                            <a
                                                href={telegramLinkUrl}
                                                target="_blank"
                                                rel="noopener noreferrer"
                                                className="flex items-center justify-center gap-2 w-full bg-sky-500 hover:bg-sky-600 text-white font-semibold text-sm rounded-md h-10"
                                            >
                                                <Send className="h-4 w-4" />
                                                {t('Abrir Telegram y tocar "Iniciar"', 'Open Telegram and tap "Start"')}
                                            </a>
                                            <p className="text-[11px] text-gray-500">
                                                {t('El link vence en 15 minutos. Esta pantalla se actualiza sola cuando el bot confirma.', 'The link expires in 15 minutes. This screen updates by itself once the bot confirms.')}
                                            </p>
                                        </>
                                    ) : (
                                        <Button
                                            onClick={handleTelegramLink}
                                            disabled={telegramBusy}
                                            className="w-full bg-sky-500 hover:bg-sky-600 text-white font-semibold"
                                        >
                                            {t('Vincular con Telegram', 'Link with Telegram')}
                                        </Button>
                                    )}
                                </div>
                            ) : (
                                <div className="bg-[#2c2c2e] p-4 rounded-xl border border-white/5 shadow-sm space-y-3">
                                    <div className="flex items-center justify-between">
                                        <div className="space-y-1 max-w-[80%] pr-4">
                                            <div className="flex items-center gap-2">
                                                <Send className={`h-4 w-4 ${telegram.enabled ? 'text-sky-400' : 'text-zinc-500'}`} />
                                                <span className="font-semibold text-sm">
                                                    {t('Alertas por Telegram', 'Telegram Alerts')}
                                                </span>
                                            </div>
                                            <p className="text-xs text-gray-400 leading-snug">
                                                {telegram.telegram_username ? `@${telegram.telegram_username} · ` : ''}
                                                {t('Las mismas alertas que por push, con las mismas preferencias.', 'The same alerts as push, with the same preferences.')}
                                            </p>
                                        </div>
                                        <Switch
                                            checked={telegram.enabled}
                                            disabled={telegramBusy}
                                            onCheckedChange={handleTelegramToggle}
                                            className="data-[state=checked]:bg-sky-500"
                                        />
                                    </div>
                                    <Button
                                        variant="ghost"
                                        onClick={handleTelegramUnlink}
                                        disabled={telegramBusy}
                                        className="w-full h-8 text-xs text-gray-400 hover:text-red-400 hover:bg-red-500/10"
                                    >
                                        {t('Desvincular cuenta', 'Unlink account')}
                                    </Button>
                                </div>
                            )}
                        </div>
                    )}
                </div>
            </DialogContent>
        </Dialog>
    )
}
