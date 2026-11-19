"use client"

import { useState, useEffect, useCallback } from "react"
import { Download, Zap, Clock, MapPin, RefreshCw, AlertCircle, CheckCircle, Folder, Server, Activity, Database, Menu, X, Navigation, Settings, LocateFixed, AlertTriangle, Layers, Cpu, ImageIcon, Info, Bell } from 'lucide-react'
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Badge } from "@/components/ui/badge"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { Switch } from "@/components/ui/switch"
import { fetchImages, fetchStatus, ApiStatus, ApiImages, StormCell, fetchReports, WeatherReport, updateLocation, AppNotification, fetchNotifications } from "@/lib/api"
import { useAuth } from "@/lib/auth-context"
import Link from "next/link"
import { useLanguage } from "@/lib/language-context"
import dynamic from "next/dynamic"
import { MapErrorBoundary } from "@/components/map-error-boundary"

// Import the new StoryControls component
import { StoryControls } from "@/components/story-controls"

// Lazy load non-critical and heavy components
const RadarVisualization = dynamic(() => import("@/components/radar-visualization").then(mod => mod.RadarVisualization), { ssr: false })
const AdminCommentBar = dynamic(() => import("@/components/admin-comment-bar").then(mod => mod.AdminCommentBar), { ssr: false })
const ReportDialog = dynamic(() => import("@/components/report-dialog").then(mod => mod.ReportDialog), { ssr: false })
const PushSubscriptionButton = dynamic(() => import("@/components/push-subscription-button").then(mod => mod.PushSubscriptionButton), { ssr: false })
const TutorialDialog = dynamic(() => import("@/components/tutorial-dialog").then(mod => mod.TutorialDialog), { ssr: false })
const SettingsDialog = dynamic(() => import("@/components/settings-dialog").then(mod => mod.SettingsDialog), { ssr: false })
const WeatherSidebar = dynamic(() => import("@/components/weather-sidebar").then(mod => mod.WeatherSidebar), { ssr: false })
const NotificationsDialog = dynamic(() => import("@/components/notifications-dialog").then(mod => mod.NotificationsDialog), { ssr: false })

function getDistanceFromLatLonInKm(lat1: number, lon1: number, lat2: number, lon2: number) {
  var R = 6371;
  var dLat = deg2rad(lat2 - lat1);
  var dLon = deg2rad(lon2 - lon1);
  var a =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(deg2rad(lat1)) * Math.cos(deg2rad(lat2)) *
    Math.sin(dLon / 2) * Math.sin(dLon / 2)
    ;
  var c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  var d = R * c;
  return d;
}

function deg2rad(deg: number) {
  return deg * (Math.PI / 180)
}

export default function StoryModePage() {
  const { t } = useLanguage()
  const [status, setStatus] = useState<ApiStatus | null>(null)
  const [images, setImages] = useState<ApiImages>({ input_images: [], prediction_images: [] })
  const [error, setError] = useState<string | null>(null)
  const { user, token, logout } = useAuth()
  const [isSidebarOpen, setIsSidebarOpen] = useState(false)
  const [isReportDialogOpen, setIsReportDialogOpen] = useState(false)
  const [isTutorialOpen, setIsTutorialOpen] = useState(false)
  const [isSettingsOpen, setIsSettingsOpen] = useState(false)
  const [reports, setReports] = useState<WeatherReport[]>([])
  const [showReports, setShowReports] = useState(true)
  const [showStormCells, setShowStormCells] = useState(true)
  const [showWeatherWidget, setShowWeatherWidget] = useState(true)
  const [showStations, setShowStations] = useState(true)
  const [showSmoothRadar, setShowSmoothRadar] = useState(false)
  
  // Preview overlay state (for storm exploration before model execution)
  const [previewOverlay, setPreviewOverlay] = useState<{ url: string; bounds: number[][] } | null>(null)

  // Notifications State
  const [notifications, setNotifications] = useState<AppNotification[]>([])
  const [isNotificationsOpen, setIsNotificationsOpen] = useState(false)
  const [selectedNotificationId, setSelectedNotificationId] = useState<number | null>(null)
  const [unreadCount, setUnreadCount] = useState(0)

  // Geolocation & Storm Logic
  const [userLocation, setUserLocation] = useState<{ lat: number, lon: number } | null>(null)
  const [nearestStorm, setNearestStorm] = useState<{ distance: number, cell: StormCell } | null>(null)
  const [locationError, setLocationError] = useState<string | null>(null)
  const [isMounted, setIsMounted] = useState(false)

  // Geolocation with Fallback Strategy
  useEffect(() => {
    setIsMounted(true)

    const hasSeenTutorial = localStorage.getItem('hasSeenTutorial');
    if (!hasSeenTutorial) {
      setIsTutorialOpen(true);
      localStorage.setItem('hasSeenTutorial', 'true');
    }

    if (user) {
      const savedStormCells = localStorage.getItem('showStormCells');
      if (savedStormCells !== null) setShowStormCells(savedStormCells === 'true');

      const savedReports = localStorage.getItem('showReports');
      if (savedReports !== null) setShowReports(savedReports === 'true');

      const savedWeatherWidget = localStorage.getItem('showWeatherWidget');
      if (savedWeatherWidget !== null) setShowWeatherWidget(savedWeatherWidget === 'true');

      const savedStations = localStorage.getItem('showStations');
      if (savedStations !== null) setShowStations(savedStations === 'true');

      const savedSmoothRadar = localStorage.getItem('showSmoothRadar');
      if (savedSmoothRadar !== null) setShowSmoothRadar(savedSmoothRadar === 'true');
    } else {
      setShowStormCells(true);
      setShowReports(true);
      setShowWeatherWidget(true);
      setShowStations(true);
      setShowSmoothRadar(false);
    }

    const getLocation = (highAccuracy = true) => {
      if (!("geolocation" in navigator)) {
        setLocationError(t("Navegador sin soporte GPS", "Browser without GPS support"));
        return;
      }

      navigator.geolocation.getCurrentPosition(
        async (position) => {
          const { latitude, longitude } = position.coords;
          setUserLocation({ lat: latitude, lon: longitude });
          setLocationError(null);

          if (user && token) {
            try {
              await updateLocation(latitude, longitude, token);
            } catch (e) {
              console.error("Failed to send location", e);
            }
          }
        },
        (error) => {
          if (highAccuracy) {
            getLocation(false);
          } else {
            let msg = t("Ubicación no disponible", "Location unavailable");
            if (error.code === 1) msg = t("Permiso de GPS denegado", "GPS permission denied");
            if (error.code === 3) msg = t("Tiempo de espera agotado (GPS)", "GPS timeout");
            setLocationError(msg);
          }
        },
        {
          enableHighAccuracy: highAccuracy,
          timeout: highAccuracy ? 5000 : 15000,
          maximumAge: highAccuracy ? 0 : 600000
        }
      );
    }

    getLocation(true);
    const intervalId = setInterval(() => getLocation(true), 1000 * 60 * 5);
    return () => clearInterval(intervalId);
  }, [user, token, t]);

  useEffect(() => {
    fetchNotifications().then(data => {
      setNotifications(data)
      if (typeof window !== 'undefined') {
        const lastRead = localStorage.getItem('lastReadNotificationId');
        const lastReadId = lastRead ? parseInt(lastRead, 10) : 0;
        const unread = data.filter(n => n.id > lastReadId).length;
        setUnreadCount(unread);
      }
    })
  }, [])

  useEffect(() => {
    if (!userLocation) return;
    let allCells: StormCell[] = [];
    const lastInput = images.input_images[images.input_images.length - 1];
    if (lastInput?.cells) {
      allCells.push(...lastInput.cells);
    }

    if (allCells.length === 0) {
      setNearestStorm(null);
      return;
    }

    let minDist = Infinity;
    let closestCell: StormCell | null = null;
    for (const cell of allCells) {
      const d = getDistanceFromLatLonInKm(userLocation.lat, userLocation.lon, cell.lat, cell.lon);
      if (d < minDist) {
        minDist = d;
        closestCell = cell;
      }
    }

    if (closestCell && minDist <= 50 && closestCell.max_dbz > 50) {
      setNearestStorm({
        distance: minDist,
        cell: closestCell
      });
    } else {
      setNearestStorm(null);
    }
  }, [images, userLocation]);

  const fetchData = useCallback(async () => {
    try {
      const statusData = await fetchStatus()
      const imagesData = await fetchImages(token || undefined)
      const reportsData = await fetchReports()
      setStatus(statusData)
      setImages(imagesData)
      setReports(reportsData)
      setError(null)
    } catch (e) {
      setError(t("Error al conectar con el servidor.", "Failed to connect to the backend API. Is it running?"))
      console.error(e)
    }
  }, [t, token])

  useEffect(() => {
    fetchData() 
    const interval = setInterval(fetchData, 5000) 
    return () => clearInterval(interval) 
  }, [fetchData])

  const bufferSize = status?.files_in_buffer ?? 0
  const bufferMaxSize = status?.files_needed_for_run ?? 8
  const lastPredictionTime = status?.last_update
    ? new Date(status.last_update).toLocaleTimeString()
    : "--:--"

  return (
    <div className="relative h-dvh w-screen overflow-hidden bg-black font-sans">

      {/* Full Screen Map */}
      <div className="absolute inset-0 z-0">
        <MapErrorBoundary>
        <RadarVisualization
          inputFiles={images.input_images}
          predictionFiles={images.prediction_images}
          isProcessing={!!(status?.status?.includes("PROCESSING") || status?.status?.includes("PREDICTING"))}
          reports={showReports ? reports : undefined}
          showStormCells={showStormCells}
          showStations={showStations}
          showSmoothRadar={showSmoothRadar}
          userLocation={userLocation}
          nearestStorm={nearestStorm}
          onReportUpdate={fetchData}
          previewOverlay={previewOverlay}
        />
        </MapErrorBoundary>
      </div>

      {/* Floating Navbar & Alerts - Z-Index Higher than Map */}
      <div className="absolute top-0 left-0 right-0 z-40 pointer-events-none flex flex-col items-center">
        <div className="w-full max-w-[1700px] mx-auto flex justify-between items-center pointer-events-auto p-4 z-50">
          <div className="flex items-center">
            {/* Nav back to main */}
            <Link href="/">
              <Button variant="ghost" className="bg-black/40 backdrop-blur-md border border-white/10 text-white hover:bg-white/20 h-9 shrink-0 gap-2">
                <Navigation className="w-4 h-4 rotate-180" />
                <span className="hidden sm:inline">{t("Volver al Radar Vivo", "Back to Live Radar")}</span>
              </Button>
            </Link>
          </div>

          <div className="flex items-center gap-1 sm:gap-2">
            {user?.role === 'superadmin' && (
              <div className="hidden md:flex items-center space-x-2 px-3 py-1.5 rounded-full bg-purple-900/40 backdrop-blur-md border border-purple-500/30 h-9">
                <div className="w-2 h-2 rounded-full bg-purple-400 animate-pulse" />
                <span className="text-xs text-purple-200 font-semibold tracking-wider uppercase">
                  {t("STORY MODE", "STORY MODE")}
                </span>
              </div>
            )}
            
            {user ? (
              <div className="flex items-center gap-1 sm:gap-2 bg-black/40 backdrop-blur-md border border-white/10 p-1 sm:p-1.5 rounded-xl">
                <span className="text-xs text-gray-300 px-2 hidden sm:inline-block">{t(`Hola, ${user.username}`, `Hello, ${user.username}`)}</span>
                <Button variant="ghost" size="sm" onClick={logout} className="h-6 px-2 text-xs hover:bg-white/10 text-white shrink-0">
                  {t("Salir", "Logout")}
                </Button>
              </div>
            ) : null}
          </div>
        </div>
      </div>

      {/* STORY MODE CONTROLS OVERLAY - Left side, below nav button */}
      <div className="absolute top-[80px] left-4 z-50 pointer-events-auto">
        <StoryControls
          currentStatus={status}
          onPreviewChange={setPreviewOverlay}
        />
      </div>

      {/* Error Toast (Floating) */}
      {error && (
        <div className="absolute top-20 left-1/2 transform -translate-x-1/2 z-50 w-[90%] max-w-md">
          <Alert variant="destructive" className="bg-red-900/80 backdrop-blur-md border-red-500/50 text-white shadow-xl">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        </div>
      )}
    </div>
  )
}

function PipelineStep({ label, active, icon }: any) {
  return (
    <div className={`flex items-center justify-between p-2 rounded-lg transition-colors ${active ? 'bg-red-500/20 border border-red-500/40' : 'bg-transparent'}`}>
      <div className="flex items-center space-x-3">
        <div className={`p-1.5 rounded-md ${active ? 'bg-red-600 text-white' : 'bg-white/10 text-red-300'}`}>
          {icon}
        </div>
        <span className={`text-sm ${active ? 'text-red-100 font-bold' : 'text-red-400/80'}`}>{label}</span>
      </div>
      {active && <Activity className="h-3 w-3 text-red-500 animate-pulse" />}
    </div>
  )
}
