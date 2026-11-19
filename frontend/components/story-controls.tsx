"use client"

import { useState, useEffect, useCallback } from "react"
import { Play, Square, Loader2, ListVideo, Pause, PlayCircle, AlertTriangle, CheckCircle2, Cog, ChevronLeft, ChevronRight, Eye, X, PanelRightOpen } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Progress } from "@/components/ui/progress"
import { Badge } from "@/components/ui/badge"
import { useAuth } from "@/lib/auth-context"
import { ApiStatus } from "@/lib/api"
import { useLanguage } from "@/lib/language-context"

interface Story {
  id: string;
  name: string;
  name_es?: string;
  name_en?: string;
  file_count: number;
  time_range: string;
}

interface StoryStatus {
  is_active: boolean;
  story_id: string | null;
  current_index: number;
  total_frames: number;
  progress_percent: number;
  conversion_status: string;
  conversion_error: string | null;
  worker_paused: boolean;
  stop_scheduled_at: string | null;
  last_feed_time: string | null;
  preview_status?: string;
  preview_story_id?: string;
}

interface PreviewItem {
  index: number;
  timestamp: string;
  time_display: string;
  url: string;
  bounds: number[][] | null;
}

type Phase = "SETUP" | "PREVIEW" | "ACTIVE"

interface StoryControlsProps {
  currentStatus: ApiStatus | null;
  onPreviewChange?: (preview: { url: string; bounds: number[][] } | null) => void;
}

export function StoryControls({ currentStatus, onPreviewChange }: StoryControlsProps) {
  const { token, user } = useAuth()
  const { t } = useLanguage()
  const [stories, setStories] = useState<Story[]>([])
  const [selectedStory, setSelectedStory] = useState<string>("")
  
  const [storyStatus, setStoryStatus] = useState<StoryStatus | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  // Preview state
  const [previews, setPreviews] = useState<PreviewItem[]>([])
  const [previewIndex, setPreviewIndex] = useState(0)
  const [previewStatus, setPreviewStatus] = useState<string>("idle")

  // UI state
  const [isCollapsed, setIsCollapsed] = useState(false)
  const [confirmStart, setConfirmStart] = useState(false)

  // Detect mobile on mount
  useEffect(() => {
    if (typeof window !== 'undefined' && window.innerWidth < 768) {
      setIsCollapsed(true)
    }
  }, [])

  // Derive phase from state
  const phase: Phase = storyStatus?.is_active ? "ACTIVE" 
    : previews.length > 0 ? "PREVIEW" 
    : "SETUP"

  // Fetch available stories
  useEffect(() => {
    fetch('/api/story/list')
      .then(res => res.json())
      .then(data => {
        if (data.stories) {
          setStories(data.stories)
          if (data.stories.length > 0) {
              setSelectedStory(data.stories[0].id)
          }
        }
      })
      .catch(console.error)
  }, [])

  // Poll story status
  useEffect(() => {
    const pollStatus = async () => {
      try {
        const res = await fetch('/api/story/status')
        if (res.ok) {
          const data = await res.json()
          setStoryStatus(data)
        }
      } catch (e) {
        console.error(e)
      }
    }
    pollStatus()
    const interval = setInterval(pollStatus, 3000)
    return () => clearInterval(interval)
  }, [])

  // Poll previews when in generating state or preview phase
  useEffect(() => {
    if (previewStatus !== "generating" && phase !== "PREVIEW") return
    if (!selectedStory) return

    const pollPreviews = async () => {
      try {
        const res = await fetch(`/api/story/previews/${selectedStory}`)
        if (res.ok) {
          const data = await res.json()
          setPreviews(data.previews || [])
          setPreviewStatus(data.preview_status || "idle")
        }
      } catch (e) {
        console.error(e)
      }
    }
    pollPreviews()
    const interval = setInterval(pollPreviews, previewStatus === "generating" ? 2000 : 10000)
    return () => clearInterval(interval)
  }, [previewStatus, selectedStory, phase])

  // Emit preview change to parent (for map overlay)
  useEffect(() => {
    if (phase === "PREVIEW" && previews.length > 0 && onPreviewChange) {
      const current = previews[previewIndex]
      if (current?.bounds) {
        onPreviewChange({ url: current.url, bounds: current.bounds })
      }
    } else if (phase !== "PREVIEW" && onPreviewChange) {
      onPreviewChange(null)
    }
  }, [phase, previewIndex, previews, onPreviewChange])

  // --- Actions ---
  const handlePrepare = async () => {
    if (!token || !selectedStory) return
    setIsLoading(true)
    setActionError(null)
    try {
      const res = await fetch('/api/story/prepare', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({ story_id: selectedStory })
      })
      const data = await res.json()
      if (!res.ok) {
        setActionError(data.error || "Error")
        return
      }
      if (data.status === "ready") {
        // Previews already cached, load them
        const previewRes = await fetch(`/api/story/previews/${selectedStory}`)
        if (previewRes.ok) {
          const previewData = await previewRes.json()
          setPreviews(previewData.previews || [])
          setPreviewIndex(0)
          setPreviewStatus("ready")
        }
      } else {
        setPreviewStatus("generating")
        setPreviewIndex(0)
      }
    } catch (e) {
      setActionError("Error de conexión")
      console.error(e)
    } finally {
      setIsLoading(false)
    }
  }

  const handleStart = async () => {
    if (!token) return
    if (!confirmStart) {
      setConfirmStart(true)
      return
    }
    
    setIsLoading(true)
    setActionError(null)
    try {
      const res = await fetch('/api/story/start', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({ 
          story_id: selectedStory,
          start_index: phase === "PREVIEW" ? previewIndex : undefined
        })
      })
      const data = await res.json()
      if (!res.ok) {
        setActionError(data.error || "Error desconocido")
      }
      setConfirmStart(false)
      // Clear preview state when transitioning to ACTIVE
      setPreviews([])
      if (onPreviewChange) onPreviewChange(null)
    } catch (e) {
      setActionError("Error de conexión")
      console.error(e)
    } finally {
      setIsLoading(false)
    }
  }

  const handleStop = async () => {
    if (!token) return
    setIsLoading(true)
    setActionError(null)
    try {
      await fetch('/api/story/stop', {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` }
      })
    } catch (e) {
      console.error(e)
    } finally {
      setIsLoading(false)
    }
  }

  const handleWorkerToggle = async () => {
    if (!token) return
    setIsLoading(true)
    setActionError(null)
    const endpoint = storyStatus?.worker_paused 
      ? '/api/story/worker/resume' 
      : '/api/story/worker/pause'
    try {
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}` }
      })
      if (!res.ok) {
        const data = await res.json()
        setActionError(data.error || "Error")
      }
    } catch (e) {
      console.error(e)
    } finally {
      setIsLoading(false)
    }
  }

  const handleBackToSetup = () => {
    setPreviews([])
    setPreviewIndex(0)
    setPreviewStatus("idle")
    setConfirmStart(false)
    if (onPreviewChange) onPreviewChange(null)
  }

  // Only show for superadmin
  if (user?.role !== 'superadmin') {
    return null;
  }

  const selectedStoryInfo = stories.find(s => s.id === selectedStory)
  const currentPreview = previews[previewIndex]

  // Conversion status indicator
  const ConversionBadge = () => {
    const status = storyStatus?.conversion_status
    if (status === "converting") return (
      <Badge variant="outline" className="bg-yellow-900/30 border-yellow-500/40 text-yellow-300 gap-1">
        <Loader2 className="w-3 h-3 animate-spin" />
        {t("Convirtiendo MDV...", "Converting MDV...")}
      </Badge>
    )
    if (status === "ready") return (
      <Badge variant="outline" className="bg-green-900/30 border-green-500/40 text-green-300 gap-1">
        <CheckCircle2 className="w-3 h-3" />
        {t("Listo", "Ready")}
      </Badge>
    )
    if (status === "error") return (
      <Badge variant="outline" className="bg-red-900/30 border-red-500/40 text-red-300 gap-1">
        <AlertTriangle className="w-3 h-3" />
        {t("Error", "Error")}
      </Badge>
    )
    return null
  }

  // ========== COLLAPSED (mobile toggle button) ==========
  if (isCollapsed) {
    return (
      <button
        onClick={() => setIsCollapsed(false)}
        className="flex items-center gap-2 bg-black/80 backdrop-blur-xl border border-purple-500/40 rounded-xl px-3 py-2.5 shadow-2xl hover:bg-purple-900/40 transition-all"
      >
        <PanelRightOpen className="w-5 h-5 text-purple-400" />
        <span className="text-xs text-purple-300 font-semibold">
          {t("Modo Historia", "Story Mode")}
        </span>
        {phase === "ACTIVE" && storyStatus && (
          <Badge variant="outline" className="bg-purple-900/50 border-purple-500/40 text-purple-200 text-[10px] px-1.5 py-0">
            {storyStatus.progress_percent?.toFixed(0)}%
          </Badge>
        )}
      </button>
    )
  }

  // ========== EXPANDED PANEL ==========
  return (
    <Card className="bg-black/80 backdrop-blur-xl border-purple-500/30 text-white w-[320px] sm:w-[360px] shadow-2xl max-h-[calc(100vh-120px)] overflow-y-auto">
      <CardHeader className="pb-3 border-b border-white/10">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-purple-400">
            <ListVideo className="w-5 h-5" />
            {t("Modo Historia", "Story Mode")}
          </CardTitle>
          <button 
            onClick={() => setIsCollapsed(true)}
            className="p-1 rounded hover:bg-white/10 text-zinc-400 hover:text-white transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <CardDescription className="text-zinc-400">
          {t("Simulador de tormentas pasadas", "Past storm simulator")}
        </CardDescription>
      </CardHeader>
      
      <CardContent className="pt-4 space-y-4">
        {/* Worker Status / Pause Button */}
        <div className="flex items-center justify-between bg-zinc-900/60 rounded-lg p-2.5 border border-white/5">
          <div className="flex items-center gap-2">
            <Cog className={`w-4 h-4 ${storyStatus?.worker_paused ? 'text-yellow-400' : 'text-green-400'}`} />
            <div>
              <div className="text-xs font-medium text-zinc-300">
                {t("Worker Normal", "Normal Worker")}
              </div>
              <div className={`text-[10px] ${storyStatus?.worker_paused ? 'text-yellow-400' : 'text-green-400'}`}>
                {storyStatus?.worker_paused 
                  ? t("⏸ Pausado", "⏸ Paused") 
                  : t("▶ Activo", "▶ Active")}
              </div>
            </div>
          </div>
          <Button
            variant="outline"
            size="sm"
            className={`h-7 text-xs border-white/10 ${
              storyStatus?.worker_paused 
                ? 'bg-green-900/30 hover:bg-green-800/40 text-green-300' 
                : 'bg-yellow-900/30 hover:bg-yellow-800/40 text-yellow-300'
            }`}
            onClick={handleWorkerToggle}
            disabled={isLoading}
          >
            {storyStatus?.worker_paused 
              ? <><PlayCircle className="w-3 h-3 mr-1" />{t("Reanudar", "Resume")}</>
              : <><Pause className="w-3 h-3 mr-1" />{t("Pausar", "Pause")}</>
            }
          </Button>
        </div>

        {/* Error Display */}
        {actionError && (
          <div className="bg-red-900/30 border border-red-500/30 rounded-lg p-2.5 text-xs text-red-300 flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
            <span>{actionError}</span>
          </div>
        )}

        {/* ========== PHASE: ACTIVE ========== */}
        {phase === "ACTIVE" && storyStatus && (
          <div className="space-y-3">
            <div className="flex justify-center">
              <ConversionBadge />
            </div>

            {/* Progress */}
            <div className="bg-purple-900/30 border border-purple-500/20 rounded-lg p-3">
              <div className="flex justify-between items-center mb-2">
                <span className="text-xs text-purple-300 font-semibold uppercase tracking-widest">
                  {t("Progreso", "Progress")}
                </span>
                <span className="text-xs text-zinc-400 font-mono">
                  {storyStatus.current_index + 1} / {storyStatus.total_frames}
                </span>
              </div>
              <Progress 
                value={storyStatus.progress_percent} 
                className="h-2 bg-purple-900/50"
              />
              <div className="text-center mt-2">
                <span className="text-lg font-bold text-white font-mono">
                  {storyStatus.progress_percent.toFixed(0)}%
                </span>
              </div>
            </div>

            {/* Current Story Info */}
            <div className="text-center space-y-1">
              <div className="text-xs text-zinc-400">
                {storyStatus.story_id}
              </div>
              <div className="text-[10px] text-zinc-500">
                {t("1 frame cada 60 segundos", "1 frame every 60 seconds")}
              </div>
            </div>

            {storyStatus.conversion_error && (
              <div className="bg-red-900/30 border border-red-500/30 rounded p-2 text-xs text-red-300">
                {storyStatus.conversion_error}
              </div>
            )}
            
            {/* Worker Status */}
            <div className="bg-black/50 p-2 rounded border border-white/5 text-xs font-mono text-zinc-500 text-center">
              Worker: {currentStatus?.status || "OFFLINE"}
            </div>

            {/* Stop Button */}
            <Button 
                variant="destructive" 
                className="w-full flex items-center justify-center gap-2 h-11"
                onClick={handleStop}
                disabled={isLoading}
            >
              {isLoading ? <Loader2 className="w-5 h-5 animate-spin" /> : <Square className="w-5 h-5" />}
              <span className="font-bold">{t("Detener Historia", "Stop Story")}</span>
            </Button>
          </div>
        )}

        {/* ========== PHASE: PREVIEW ========== */}
        {phase === "PREVIEW" && (
          <div className="space-y-3">
            {/* Preview generation progress */}
            {previewStatus === "generating" && (
              <div className="bg-yellow-900/20 border border-yellow-500/20 rounded-lg p-2.5 text-center space-y-1.5">
                <div className="flex items-center justify-center gap-2 text-yellow-300 text-xs">
                  <Loader2 className="w-3 h-3 animate-spin" />
                  {t("Generando previews...", "Generating previews...")}
                </div>
                <div className="text-[10px] text-yellow-400/70">
                  {previews.length} / {selectedStoryInfo?.file_count || "?"} {t("imágenes", "images")}
                </div>
                <Progress 
                  value={selectedStoryInfo ? (previews.length / selectedStoryInfo.file_count) * 100 : 0} 
                  className="h-1.5 bg-yellow-900/30"
                />
              </div>
            )}

            {/* Image navigator */}
            {previews.length > 0 && (
              <>
                {/* Scan position indicator */}
                <div className="text-center">
                  <div className="text-xs text-zinc-400">
                    Scan <span className="text-white font-bold font-mono">{previewIndex + 1}</span> / {previews.length}
                  </div>
                  {currentPreview && (
                    <div className="text-sm text-cyan-400 font-mono font-semibold mt-0.5">
                      {currentPreview.time_display}
                    </div>
                  )}
                </div>

                {/* Navigation controls */}
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-9 px-3 border-white/10 bg-zinc-900/50 hover:bg-zinc-800 text-white"
                    onClick={() => setPreviewIndex(Math.max(0, previewIndex - 1))}
                    disabled={previewIndex === 0}
                  >
                    <ChevronLeft className="w-4 h-4" />
                  </Button>
                  
                  {/* Slider */}
                  <input 
                    type="range"
                    min={0}
                    max={previews.length - 1}
                    value={previewIndex}
                    onChange={(e) => setPreviewIndex(parseInt(e.target.value))}
                    className="flex-1 h-2 bg-zinc-700 rounded-lg appearance-none cursor-pointer accent-purple-500"
                  />
                  
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-9 px-3 border-white/10 bg-zinc-900/50 hover:bg-zinc-800 text-white"
                    onClick={() => setPreviewIndex(Math.min(previews.length - 1, previewIndex + 1))}
                    disabled={previewIndex >= previews.length - 1}
                  >
                    <ChevronRight className="w-4 h-4" />
                  </Button>
                </div>

                {/* Scan info */}
                <div className="bg-zinc-900/50 border border-white/5 rounded-lg p-2 text-center text-[10px] text-zinc-500">
                  {t(
                    `El modelo necesita 8 scans consecutivos. Se ejecutará desde el scan ${previewIndex + 1}.`,
                    `Model needs 8 consecutive scans. Will run from scan ${previewIndex + 1}.`
                  )}
                </div>
              </>
            )}

            {/* Start from here button */}
            {previews.length > 0 && (
              <Button 
                className={`w-full flex items-center justify-center gap-2 h-11 shadow-lg transition-all ${
                  confirmStart 
                    ? 'bg-green-600 hover:bg-green-700 shadow-[0_0_20px_rgba(34,197,94,0.3)]' 
                    : 'bg-purple-600 hover:bg-purple-700 shadow-[0_0_20px_rgba(147,51,234,0.3)]'
                } text-white`}
                onClick={handleStart}
                disabled={isLoading}
              >
                {isLoading ? (
                  <Loader2 className="w-5 h-5 animate-spin" />
                ) : confirmStart ? (
                  <CheckCircle2 className="w-5 h-5" />
                ) : (
                  <Play className="w-5 h-5" />
                )}
                <span className="font-bold">
                  {confirmStart 
                    ? t("Confirmar Inicio", "Confirm Start") 
                    : t("Iniciar desde aquí", "Start from here")}
                </span>
              </Button>
            )}
            {confirmStart && (
              <button 
                className="w-full text-xs text-zinc-500 hover:text-zinc-300 transition-colors"
                onClick={() => setConfirmStart(false)}
              >
                {t("Cancelar", "Cancel")}
              </button>
            )}

            {/* Back button */}
            <button 
              className="w-full text-xs text-zinc-500 hover:text-zinc-300 transition-colors flex items-center justify-center gap-1 pt-1"
              onClick={handleBackToSetup}
            >
              <ChevronLeft className="w-3 h-3" />
              {t("Volver a selección", "Back to selection")}
            </button>
          </div>
        )}

        {/* ========== PHASE: SETUP ========== */}
        {phase === "SETUP" && !storyStatus?.is_active && (
          <div className="space-y-4">
            {/* Storm Selector */}
            <div className="space-y-1.5">
              <label className="text-xs text-zinc-400 uppercase tracking-wider">
                {t("Seleccionar Tormenta", "Select Storm")}
              </label>
              <Select value={selectedStory} onValueChange={(v) => { setSelectedStory(v); setConfirmStart(false); }}>
                <SelectTrigger className="bg-black/50 border-white/10 text-white">
                  <SelectValue placeholder={t("Selecciona...", "Select...")} />
                </SelectTrigger>
                <SelectContent className="bg-zinc-900 border-zinc-800 text-white">
                  {stories.map(s => (
                    <SelectItem key={s.id} value={s.id}>
                      <div className="flex flex-col">
                        <span className="font-medium">{t(s.name_es || s.name, s.name_en || s.name)}</span>
                        <span className="text-[10px] text-zinc-400">
                          {s.file_count} escaneos • {s.time_range}
                        </span>
                      </div>
                    </SelectItem>
                  ))}
                  {stories.length === 0 && (
                    <SelectItem value="none" disabled>
                      {t("No hay tormentas disponibles", "No storms available")}
                    </SelectItem>
                  )}
                </SelectContent>
              </Select>
            </div>

            {/* Selected Storm Details */}
            {selectedStoryInfo && (
              <div className="bg-zinc-900/50 border border-white/5 rounded-lg p-2.5 space-y-1">
                <div className="text-xs text-zinc-300 font-medium">{t(selectedStoryInfo.name_es || selectedStoryInfo.name, selectedStoryInfo.name_en || selectedStoryInfo.name)}</div>
                <div className="text-[10px] text-zinc-500">{selectedStoryInfo.time_range}</div>
                <div className="text-[10px] text-zinc-500">
                  {selectedStoryInfo.file_count} {t("escaneos MDV", "MDV scans")} 
                  • ~{Math.ceil((selectedStoryInfo.file_count - 7) * 1)} min {t("de reproducción", "playback")}
                </div>
              </div>
            )}

            {/* NC Cleanup Pending */}
            {storyStatus?.stop_scheduled_at && (
              <div className="bg-amber-900/20 border border-amber-500/20 rounded-lg p-2 text-[10px] text-amber-300 text-center">
                {t(
                  "Archivos NC pendientes de limpieza (se borran automáticamente)",
                  "NC files pending cleanup (auto-deleted)"
                )}
              </div>
            )}

            {/* Explore Button */}
            <Button 
              className="w-full flex items-center justify-center gap-2 h-11 shadow-lg bg-purple-600 hover:bg-purple-700 shadow-[0_0_20px_rgba(147,51,234,0.3)] text-white transition-all"
              onClick={handlePrepare}
              disabled={isLoading || !selectedStory || stories.length === 0}
            >
              {isLoading ? (
                <Loader2 className="w-5 h-5 animate-spin" />
              ) : (
                <Eye className="w-5 h-5" />
              )}
              <span className="font-bold">
                {t("Explorar tormenta", "Explore storm")}
              </span>
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
