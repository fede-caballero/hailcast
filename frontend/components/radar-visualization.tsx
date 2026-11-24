'use client'

import { useState, useEffect, useMemo, useRef, memo } from "react"
import { Button } from "@/components/ui/button"
import { Slider } from "@/components/ui/slider"
import { useToast } from "@/components/ui/use-toast"
import { Play, Pause, RotateCcw, Calendar, Clock, Trash2, MapPin, X, AlertTriangle, Pencil, Plane, Cloud, Layers, Share2, Zap, CloudRain, Heart, Thermometer, Sun, Mail, RadioTower, Film, Camera } from "lucide-react"
import { GIFEncoder, quantize, applyPalette } from "gifenc"
import { ImageWithBounds, deleteReport, fetchAircraft, fetchHailSwathToday, fetchWeatherStations, fetchSolarParks, fetchLightning, LightningCollection, WeatherReport, Aircraft, StormCell, SolarPark, toggleReportLike, RadarInfo, RadarId, DEFAULT_RADAR } from "@/lib/api"
import { Source, Layer, NavigationControl, ScaleControl, FullscreenControl, GeolocateControl, MapRef, Popup, Marker } from 'react-map-gl/maplibre'
import 'maplibre-gl/dist/maplibre-gl.css'
import { useAuth } from "@/lib/auth-context"
import { ReportDialog } from "./report-dialog"
import { useLanguage } from "@/lib/language-context"
import dynamic from "next/dynamic"

// Lazy load the Map component. It's huge.
const Map = dynamic(() => import('react-map-gl/maplibre'), {
  ssr: false,
  loading: () => <div className="w-full h-full bg-slate-900 flex items-center justify-center"><div className="w-8 h-8 rounded-full border-4 border-blue-500 border-t-transparent animate-spin"></div></div>
})

interface RadarVisualizationProps {
  inputFiles: ImageWithBounds[]
  predictionFiles: ImageWithBounds[]
  isProcessing: boolean
  reports?: WeatherReport[]
  showStormCells?: boolean
  showLightning?: boolean
  showStations?: boolean
  userLocation?: { lat: number, lon: number } | null
  nearestStorm?: { distance: number, cell: any } | null
  onContactClick?: () => void
  onReportUpdate?: () => void
  showSmoothRadar?: boolean
  previewOverlay?: { url: string; bounds: number[][] } | null
  // Radar seleccionado y lista de radares activos (el selector solo se muestra con más de uno)
  radar?: RadarId
  radars?: RadarInfo[]
  onRadarChange?: (id: RadarId) => void
}

const INITIAL_VIEW_STATE = {
  longitude: -68.016,
  latitude: -34.647,
  zoom: 8
};

// Dark Matter style for a premium look
const MAP_STYLE = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

function haversineDistance(coords1: { lat: number, lon: number }, coords2: { lat: number, lon: number }) {
  const toRad = (x: number) => x * Math.PI / 180;
  const R = 6371; // km
  const dLat = toRad(coords2.lat - coords1.lat);
  const dLon = toRad(coords2.lon - coords1.lon);
  const a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(toRad(coords1.lat)) * Math.cos(toRad(coords2.lat)) *
    Math.sin(dLon / 2) * Math.sin(dLon / 2);
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return R * c;
}

// --- Compartir como GIF: captura del lienzo del mapa cuadro a cuadro ---------------------
const GIF_WIDTH = 480;          // ancho del GIF (mantiene la proporción del mapa)
const GIF_FRAME_DELAY_MS = 500; // por cuadro; el último dura el doble

function waitForMapRender(map: any, timeoutMs = 2500): Promise<void> {
  // Resuelve cuando el mapa terminó de cargar y dibujar (idle) o al vencer el tiempo
  return new Promise((resolve) => {
    let done = false;
    const finish = () => { if (!done) { done = true; resolve(); } };
    try { map.once('idle', finish); map.triggerRepaint(); } catch { finish(); }
    setTimeout(finish, timeoutMs);
  });
}

function captureMapFrame(map: any, label: string, sublabel: string): ImageData | null {
  const src: HTMLCanvasElement = map.getCanvas();
  if (!src.width || !src.height) return null;
  const w = GIF_WIDTH;
  const h = Math.round((src.height / src.width) * w);
  const canvas = document.createElement('canvas');
  canvas.width = w; canvas.height = h;
  const g = canvas.getContext('2d');
  if (!g) return null;
  g.drawImage(src, 0, 0, w, h);
  // Rótulo superior: tipo de cuadro + hora, y marca de agua abajo
  g.fillStyle = 'rgba(0,0,0,0.6)'; g.fillRect(0, 0, w, 34);
  g.font = 'bold 15px system-ui, sans-serif'; g.fillStyle = '#ffffff'; g.textBaseline = 'middle';
  g.fillText(label, 10, 17);
  g.font = '13px system-ui, sans-serif'; g.fillStyle = '#e5e7eb'; g.textAlign = 'right';
  g.fillText(sublabel, w - 10, 17);
  g.textAlign = 'left'; g.font = 'bold 12px system-ui, sans-serif'; g.fillStyle = 'rgba(255,255,255,0.85)';
  g.fillText('hail-cast-mendoza.com', 10, h - 12);
  return g.getImageData(0, 0, w, h);
}

function encodeGif(frames: ImageData[]): Blob {
  const gif = GIFEncoder();
  frames.forEach((frame, i) => {
    const palette = quantize(frame.data, 256);
    const index = applyPalette(frame.data, palette);
    gif.writeFrame(index, frame.width, frame.height, {
      palette,
      delay: i === frames.length - 1 ? GIF_FRAME_DELAY_MS * 2 : GIF_FRAME_DELAY_MS,
    });
  });
  gif.finish();
  const bytes = gif.bytes();
  // Copia a un ArrayBuffer propio: TS no acepta Uint8Array<ArrayBufferLike> como BlobPart
  const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
  return new Blob([buffer], { type: 'image/gif' });
}

// Íconos de rayo para la capa GLM: cuatro colores por antigüedad, dibujados en un canvas y
// registrados en el estilo del mapa (MapLibre solo dibuja símbolos que existan en el sprite).
// Ventana que se muestra en el mapa (el backend conserva 60 min para las alertas)
const LIGHTNING_WINDOW_MIN = 10;
const LIGHTNING_ICONS = [
  { id: 'bolt-0', color: '#fff176' }, // < 2 min
  { id: 'bolt-1', color: '#ffd600' }, // 2-5 min
  { id: 'bolt-2', color: '#ff8f00' }, // 5-8 min
  { id: 'bolt-3', color: '#bdbdbd' }, // 8-10 min
];

function drawBoltIcon(color: string): ImageData | null {
  if (typeof document === 'undefined') return null;
  const size = 48;
  const canvas = document.createElement('canvas');
  canvas.width = size; canvas.height = size;
  const g = canvas.getContext('2d');
  if (!g) return null;
  const bolt = new Path2D('M27 2 L9 27 H22 L17 46 L39 19 H26 L31 2 Z');
  g.lineJoin = 'round'; g.lineWidth = 4; g.strokeStyle = 'rgba(0,0,0,0.85)';
  g.stroke(bolt);
  g.fillStyle = color; g.fill(bolt);
  return g.getImageData(0, 0, size, size);
}

function ensureLightningIcons(map: any) {
  if (!map || typeof map.hasImage !== 'function') return;
  for (const icon of LIGHTNING_ICONS) {
    if (map.hasImage(icon.id)) continue;
    const img = drawBoltIcon(icon.color);
    if (img) map.addImage(icon.id, img, { pixelRatio: 2 });
  }
}

export const RadarVisualization = memo(function RadarVisualization({
  inputFiles,
  predictionFiles,
  isProcessing = false,
  reports,
  showStormCells = true,
  showLightning = true,
  showStations = true,
  showSmoothRadar = false,
  userLocation,
  nearestStorm,
  onContactClick,
  onReportUpdate,
  previewOverlay,
  radar = DEFAULT_RADAR,
  radars = [],
  onRadarChange,
}: RadarVisualizationProps) {
  const { t } = useLanguage()
  const { toast } = useToast()
  const [isPlaying, setIsPlaying] = useState(true)
  // Compartir: menú (GIF o captura) y progreso de la captura del GIF
  const [shareMenuOpen, setShareMenuOpen] = useState(false)
  const [shareProgress, setShareProgress] = useState<{ done: number; total: number } | null>(null)
  const [currentFrameIndex, setCurrentFrameIndex] = useState(0)
  const [sliderDragValue, setSliderDragValue] = useState<number | null>(null) // visual position while dragging
  const [boundariesData, setBoundariesData] = useState<any>(null)

  // Hail Swath Data
  const [hailSwathData, setHailSwathData] = useState<any>(null)

  // Weather Stations Data
  const [stationsData, setStationsData] = useState<any>(null)
  const [selectedStation, setSelectedStation] = useState<any>(null)

  // Aircraft State
  const [aircraftData, setAircraftData] = useState<Aircraft[]>([])
  const [selectedAircraft, setSelectedAircraft] = useState<string | null>(null) // callsign tapped on mobile
  // Trail: Map of callsign -> array of [lon, lat] positions (last 30)
  // NOTE: Must use globalThis.Map, because 'Map' is imported from react-map-gl and shadows the native constructor
  const aircraftTrailRef = useRef<globalThis.Map<string, [number, number][]>>(new globalThis.Map())
  const [trailGeoJSON, setTrailGeoJSON] = useState<any>({ type: 'FeatureCollection', features: [] })
  const MAX_TRAIL_POINTS = 70

  // Rayos GLM (GOES-19): últimos 30 min, refresco cada 30 s mientras la capa está activa
  const [lightningData, setLightningData] = useState<LightningCollection | null>(null)

  // Parques solares (solo superadmin): estado de las alertas privadas por Telegram
  const [solarParks, setSolarParks] = useState<SolarPark[]>([])
  const [selectedPark, setSelectedPark] = useState<string | null>(null)

  const [districtsData, setDistrictsData] = useState<any>(null)
  // Satellite layer state: 'off' | 'hail' | 'visible' | 'ir'
  const [satelliteMode, setSatelliteMode] = useState<'off' | 'hail' | 'visible' | 'ir'>('off')
  const [satelliteEstimTime, setSatelliteEstimTime] = useState<string>("")
  const [selectedReport, setSelectedReport] = useState<{
    longitude: number,
    latitude: number,
    properties: any
  } | null>(null)
  const [editingReport, setEditingReport] = useState<WeatherReport | null>(null)
  const [isReportOpen, setIsReportOpen] = useState(false)
  const mapRef = useRef<MapRef>(null)
  const { user, token } = useAuth()
  const [showLocationHint, setShowLocationHint] = useState(true)

  // Nearest Storm Locator State
  const [showNearestStormMarker, setShowNearestStormMarker] = useState(false)

  // Zoom scale tracking for dynamic markers
  const [zoomLevel, setZoomLevel] = useState(INITIAL_VIEW_STATE.zoom)

  // Storm Cell Identification State
  const [selectedCell, setSelectedCell] = useState<StormCell | null>(null)

  useEffect(() => {
    if (userLocation) {
      setShowLocationHint(false);
    }
  }, [userLocation]);

  useEffect(() => {
    if (satelliteMode === 'off' || satelliteMode === 'hail') {
      setSatelliteEstimTime("");
      return;
    }
    const updateTime = () => {
      const d = new Date();
      d.setMinutes(d.getMinutes() - 30);
      d.setMinutes(Math.floor(d.getMinutes() / 10) * 10, 0, 0);
      setSatelliteEstimTime("~" + d.toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit', timeZone: 'America/Argentina/Mendoza' }));
    };
    updateTime();
    const interval = setInterval(updateTime, 60 * 1000);
    return () => clearInterval(interval);
  }, [satelliteMode]);

  // Manga de granizo: crece durante la tormenta, así que se refresca cada 2 min, al activar la capa y al cambiar de radar
  useEffect(() => {
    const load = () => fetchHailSwathToday(radar).then((data) => setHailSwathData(data)).catch(() => {});
    if (satelliteMode === 'hail') load();
    const interval = setInterval(load, 2 * 60 * 1000);
    return () => clearInterval(interval);
  }, [satelliteMode, radar]);

  // Al cambiar de radar, centrar el mapa en el radar elegido
  const radarInfo = radars.find((r) => r.id === radar);
  const lastCenteredRadar = useRef<RadarId>(DEFAULT_RADAR);
  useEffect(() => {
    if (!radarInfo || lastCenteredRadar.current === radar) return;
    lastCenteredRadar.current = radar;
    try {
      mapRef.current?.flyTo({ center: [radarInfo.lon, radarInfo.lat], zoom: 8, duration: 1200 });
    } catch { /* mapa no listo */ }
  }, [radar, radarInfo]);

  // Rayos: últimos LIGHTNING_WINDOW_MIN minutos, actualizados cada 30 s (el GLM publica cada 20 s con ~30 s de latencia)
  useEffect(() => {
    if (!showLightning) { setLightningData(null); return; }
    let cancelled = false;
    const load = () => fetchLightning(LIGHTNING_WINDOW_MIN).then((d) => { if (!cancelled) setLightningData(d); });
    load();
    const interval = setInterval(load, 30 * 1000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [showLightning]);

  // Parques solares: solo para superadmin, se refresca cada minuto
  useEffect(() => {
    if (!token || user?.role !== 'superadmin') {
      setSolarParks([]);
      return;
    }
    let cancelled = false;
    const load = () => fetchSolarParks(token).then((parks) => { if (!cancelled) setSolarParks(parks); });
    load();
    const interval = setInterval(load, 60 * 1000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [token, user?.role]);

  // Merge frames: Inputs + Predictions
  const frames = useMemo(() => {
    return [...inputFiles, ...predictionFiles];
  }, [inputFiles, predictionFiles]);

  const totalFrames = frames.length;

  // Load boundaries
  useEffect(() => {
    fetch('/boundaries.json')
      .then(res => res.json())
      .then(data => setBoundariesData(data))
      .catch(err => console.error("Failed to load boundaries", err));

    // Load Mendoza districts
    fetch('/mendoza_departamentos.geojson')
      .then(res => res.json())
      .then(data => setDistrictsData(data))
      .catch(err => console.error("Failed to load districts", err));

    // Load Hail Swath (Today)
    fetchHailSwathToday()
      .then(data => setHailSwathData(data))
      .catch(err => console.error("Failed to load hail swath", err));

    // Load Weather Stations
    if (showStations && !stationsData) {
      fetchWeatherStations()
        .then(data => {
          if (data && data.features) setStationsData(data);
        })
        .catch(err => console.error("Failed to load stations", err));
    }
    // Poll Aircraft Data
    const pollAircraft = () => {
      fetchAircraft().then((data) => {
        setAircraftData(data);

        // Build GeoJSON from backend trail directly
        const features = data.map((ac: Aircraft) => {
          if (!ac.trail || ac.trail.length < 2) return null;
          return {
            type: 'Feature' as const,
            properties: { callsign: ac.reg || ac.callsign },
            geometry: { type: 'LineString' as const, coordinates: ac.trail }
          };
        }).filter(Boolean);

        setTrailGeoJSON({ type: 'FeatureCollection', features });
      });
    };
    pollAircraft(); // Initial fetch
    const acInterval = setInterval(pollAircraft, 3000); // 3s — smoother trail
    return () => clearInterval(acInterval);
  }, []);

  // Directly update MapLibre source data imperatively (more reliable than prop updates)
  useEffect(() => {
    const map = mapRef.current?.getMap();
    // Tras perder el contexto WebGL (móviles) MapLibre deja map.style en null y getSource explota
    if (!map || !(map as any).style) return;
    try {
      const source = map.getSource('aircraft-trail-source') as any;
      if (source && typeof source.setData === 'function') {
        source.setData(trailGeoJSON);
      }
    } catch (err) {
      console.warn('[Radar] No se pudo actualizar la traza de aviones:', err);
    }
  }, [trailGeoJSON]);


  // Animation logic
  useEffect(() => {
    let interval: NodeJS.Timeout
    if (isPlaying && totalFrames > 0) {
      interval = setInterval(() => {
        setCurrentFrameIndex((prev) => {
          if (prev >= totalFrames - 1) return 0; // Loop back to start
          return prev + 1;
        })
      }, 1500) // 1500ms per frame to allow smooth crossfade
    }
    return () => clearInterval(interval)
  }, [isPlaying, totalFrames])



  // Reset when data changes significantly
  useEffect(() => {
    if (totalFrames > 0 && currentFrameIndex >= totalFrames) {
      setCurrentFrameIndex(0);
    }
  }, [totalFrames]);

  const togglePlay = () => setIsPlaying(!isPlaying)
  const resetAnimation = () => { setIsPlaying(false); setCurrentFrameIndex(0); }

  const currentImage = frames[currentFrameIndex];
  const isPrediction = currentFrameIndex >= inputFiles.length;

  const getImageCoordinates = (image: ImageWithBounds | undefined) => {
    if (!image?.bounds) return undefined;
    const b = image.bounds as any;
    const p1 = b[0];
    const p2 = b[1];

    const minLat = Math.min(p1[0], p2[0]);
    const maxLat = Math.max(p1[0], p2[0]);
    const minLon = Math.min(p1[1], p2[1]);
    const maxLon = Math.max(p1[1], p2[1]);

    return [
      [minLon, maxLat], // TL
      [maxLon, maxLat], // TR
      [maxLon, minLat], // BR
      [minLon, minLat]  // BL
    ] as [[number, number], [number, number], [number, number], [number, number]];
  }

  const imageCoordinates = useMemo(() => getImageCoordinates(currentImage), [currentImage]);

  // Calculate center of LAST OBSERVED image for proximity distance
  // Always use the last input image, NOT the current frame (which may be a prediction)
  const lastObservedImage = inputFiles[inputFiles.length - 1];
  const stormCenter = useMemo(() => {
    if (!lastObservedImage?.bounds) return null;
    const b = lastObservedImage.bounds as any;
    // Assuming bounds are [[lat1, lon1], [lat2, lon2]] or similar based on usage
    // Code above uses p1[0] as lat, p1[1] as lon
    const p1 = b[0];
    const p2 = b[1];
    return {
      lat: (p1[0] + p2[0]) / 2,
      lon: (p1[1] + p2[1]) / 2
    };
  }, [currentImage]);

  const distanceToStorm = useMemo(() => {
    if (!userLocation || !stormCenter) return null;
    return haversineDistance(
      { lat: userLocation.lat, lon: userLocation.lon },
      stormCenter
    );
  }, [userLocation, stormCenter]);

  // Sync selected report with updated reports array (so likes stay correct after re-fetch)
  useEffect(() => {
    if (selectedReport && reports) {
      const updatedReport = reports.find(r => r.id === selectedReport.properties.id);
      if (updatedReport) {
        setSelectedReport(prev => prev ? {
          ...prev,
          properties: {
            ...prev.properties,
            likes_count: updatedReport.likes_count || 0,
            user_liked: updatedReport.user_liked || false,
          }
        } : null);
      }
    }
  }, [reports]);

  const boundaryLayerStyle = {
    id: 'boundaries-layer',
    type: 'line',
    paint: {
      'line-color': '#facc15',
      'line-width': 2,
      'line-opacity': 0.6
    }
  } as const;

  // Calculate time label (approximate based on index)
  // Assuming inputs are every 15 min and predictions every 3 min (based on previous context)
  // But for simplicity in UI, we'll just show "Past" vs "Forecast +X min"
  const getTimeLabel = () => {
    if (previewOverlay) {
      return t("Modo Historia", "Story Mode");
    }
    if (isOffline) {
      const lastInput = inputFiles && inputFiles.length > 0 ? inputFiles[inputFiles.length - 1] : null;
      if (lastInput && lastInput.target_time) {
        return `${t("Radar apagado", "Radar offline")} (Última: ${lastInput.target_time})`;
      }
      return t("Radar apagado", "Radar offline");
    }
    if (!currentImage) return t("Esperando datos del radar...", "Waiting for radar data...");

    if (currentImage.target_time) {
      if (isPrediction) {
        return `${t("Pronóstico", "Forecast")} ${currentImage.target_time}`;
      } else {
        return `${t("Observación", "Observation")} ${currentImage.target_time}`;
      }
    }
    return ""; // Fallback empty
  };

  // --- Offline Check Logic ---
  const isOffline = useMemo(() => {
    // Find the latest INPUT image (observed data)
    if (!inputFiles || inputFiles.length === 0) return false;

    // Sort by timestamp if available to be sure we get the latest
    // Input files are usually sorted chronologically in the prop, but let's be safe or just take the last one
    const latestInput = inputFiles[inputFiles.length - 1];

    if (!latestInput.timestamp_iso) return false; // Can't determine

    try {
      const lastTime = new Date(latestInput.timestamp_iso).getTime();
      const now = new Date().getTime();
      const diffMinutes = (now - lastTime) / (1000 * 60);

      return diffMinutes > 15;
    } catch (e) {
      console.error("Error checking offline status", e);
      return false;
    }
  }, [inputFiles]);

  // --- Reports Layer Logic ---
  const reportsGeoJSON = useMemo(() => {
    if (!reports || reports.length === 0) return null;
    return {
      type: "FeatureCollection",
      features: reports.map(r => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [r.longitude, r.latitude] },
        properties: {
          type: r.report_type,
          id: r.id,
          description: r.description,
          username: r.username,
          image_url: r.image_url,
          likes_count: r.likes_count || 0,
          user_liked: r.user_liked || false,
          time: new Date(r.timestamp!).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        }
      }))
    };
  }, [reports]);

  const reportLayerStyle = {
    id: 'reports-layer',
    type: 'circle',
    paint: {
      'circle-radius': [
        'interpolate',
        ['linear'],
        ['zoom'],
        7, 3,   // Small at low zoom (away)
        12, 8   // Normal at high zoom (close)
      ],
      'circle-color': [
        'match',
        ['get', 'type'],
        'lluvia_debil', '#60a5fa', // Blue
        'lluvia_fuerte', '#4f46e5', // Indigo
        'granizo_pequeno', '#f97316', // Orange
        'granizo_grande', '#dc2626', // Red
        'viento_fuerte', '#94a3b8', // Slate/Grey
        'cielo_despejado', '#eab308', // Yellow
        '#ffffff' // Default white
      ],
      'circle-stroke-width': [
        'interpolate',
        ['linear'],
        ['zoom'],
        7, 1,
        12, 2
      ],
      'circle-stroke-color': '#ffffff',
      'circle-opacity': 0.9
    }
  } as const;

  // Optional: Text labels for reports
  const reportLabelStyle = {
    id: 'reports-labels',
    type: 'symbol',
    layout: {
      'text-field': ['get', 'type'], // Or use icon if we load sprite
      'text-size': 10,
      'text-offset': [0, 1.5],
      'text-variable-anchor': ['top', 'bottom', 'left', 'right'],
    },
    paint: {
      'text-color': '#ffffff',
      'text-halo-color': '#000000',
      'text-halo-width': 2
    }
  } as const;

  const districtLineStyle = {
    id: 'districts-line',
    type: 'line' as const,
    paint: {
      'line-color': '#d4d4d8', // Light Gray for districts to contrast with 30dBZ
      'line-width': 1,
      'line-opacity': 0.5,
      'line-dasharray': [2, 2]
    }
  };

  const districtLabelStyle = {
    id: 'districts-label',
    type: 'symbol' as const,
    layout: {
      'text-field': ['get', 'departamen'] as any,
      'text-size': 10,
      'text-transform': 'uppercase' as const,
      'text-offset': [0, 0] as [number, number],
      'symbol-placement': 'point' as const,
      'text-max-width': 8
    },
    paint: {
      'text-color': '#d4d4d8',
      'text-halo-color': '#000000',
      'text-halo-width': 1.5,
      'text-opacity': 0.8
    }
  };

  const SITE_URL = "https://www.hail-cast-mendoza.com/";

  const kmBetween = (lat1: number, lon1: number, lat2: number, lon2: number) => {
    const R = 6371, toRad = (x: number) => x * Math.PI / 180;
    const dLat = toRad(lat2 - lat1), dLon = toRad(lon2 - lon1);
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(a));
  };

  // Departamento que contiene el punto (ray casting sobre los polígonos ya cargados para el mapa)
  const departmentAt = (lat: number, lon: number): string | null => {
    const inRing = (ring: number[][]) => {
      let inside = false;
      for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const [xi, yi] = ring[i], [xj, yj] = ring[j];
        if ((yi > lat) !== (yj > lat) && lon < (xj - xi) * (lat - yi) / (yj - yi) + xi) inside = !inside;
      }
      return inside;
    };
    for (const f of districtsData?.features ?? []) {
      const g = f.geometry;
      const polys: number[][][][] = g?.type === 'Polygon' ? [g.coordinates] : g?.type === 'MultiPolygon' ? g.coordinates : [];
      if (polys.some(p => inRing(p[0]))) {
        const raw: string = f.properties?.departamen ?? '';
        return raw.toLowerCase().replace(/(^|\s)\S/g, c => c.toUpperCase());
      }
    }
    return null;
  };

  // Descripción del lugar de una celda: localidad si hay una a menos de 15 km, si no el departamento.
  const describePlace = async (lat: number, lon: number): Promise<string> => {
    const depto = departmentAt(lat, lon);
    try {
      const res = await fetch('/localidades.geojson');
      const data = await res.json();
      let best: { name: string, d: number } | null = null;
      for (const f of data.features ?? []) {
        const [plon, plat] = f.geometry?.coordinates ?? [];
        if (typeof plat !== 'number' || typeof plon !== 'number') continue;
        const d = kmBetween(lat, lon, plat, plon);
        if (!best || d < best.d) best = { name: f.properties?.localidad ?? '', d };
      }
      if (best && best.d <= 15) {
        return t(`cerca de ${best.name}${depto ? ` (${depto})` : ''}`, `near ${best.name}${depto ? ` (${depto})` : ''}`);
      }
    } catch { /* sin localidades: cae al departamento */ }
    if (depto) return t(`en zona rural de ${depto}`, `in rural ${depto}`);
    // Fuera de Mendoza (el radar norte llega a San Juan y San Luis): se nombra el radar que la ve
    const radarName = radarInfo?.label?.replace(/^Radar\s+/i, '') ?? '';
    return radarName
      ? t(`en el área del radar ${radarName}`, `within the ${radarName} radar area`)
      : t('en Mendoza', 'in Mendoza');
  };

  // El modo alerta solo se activa con una celda severa cerca de la ubicación del usuario
  // (nearestStorm: > 50 dBZ a menos de 50 km, calculada en page.tsx). Sin GPS, o sin celda
  // cercana, el botón comparte el radar normal: así los ecos fijos de montaña no disparan alertas.
  const hasActiveStorm = !!nearestStorm;

  // Recorre los cuadros (observados + predicción), captura cada uno y arma el GIF
  const buildShareGif = async (): Promise<File | null> => {
    const map = mapRef.current?.getMap();
    if (!map || frames.length === 0) return null;
    const wasPlaying = isPlaying;
    const prevIndex = currentFrameIndex;
    setIsPlaying(false);
    const captured: ImageData[] = [];
    try {
      for (let i = 0; i < frames.length; i++) {
        setShareProgress({ done: i, total: frames.length });
        setCurrentFrameIndex(i);
        await new Promise((r) => setTimeout(r, 80));   // que React aplique el cambio de cuadro
        await waitForMapRender(map);
        const isPred = i >= inputFiles.length;
        const label = isPred ? t('Predicción', 'Forecast') : t('Observación', 'Observed');
        const img = captureMapFrame(map, label, frames[i]?.target_time || '');
        if (img) captured.push(img);
      }
      if (!captured.length) return null;
      setShareProgress({ done: frames.length, total: frames.length });
      await new Promise((r) => setTimeout(r, 20));      // deja pintar el 100 % antes de codificar
      const blob = encodeGif(captured);
      return new File([blob], 'hailcast-radar.gif', { type: 'image/gif' });
    } catch (err) {
      console.warn('No se pudo armar el GIF:', err);
      return null;
    } finally {
      setCurrentFrameIndex(prevIndex);
      setIsPlaying(wasPlaying);
      setShareProgress(null);
    }
  };

  const handleShare = async (mode: 'radar' | 'alert' = 'radar', format: 'image' | 'gif' = 'image') => {
    setShareMenuOpen(false);
    const timeLabel = getTimeLabel();
    const utm = mode === 'alert' ? '?utm_source=share&utm_medium=alert' : '?utm_source=share&utm_medium=radar';
    // Si se comparte desde un radar que no es el primario, el link abre ese mismo radar
    const urlStr = SITE_URL + utm + (radar !== DEFAULT_RADAR ? `&radar=${encodeURIComponent(radar)}` : '');
    let textStr = t(`🌩️ Mirá la tormenta (${timeLabel}) en vivo desde el radar de HailCast:`, `🌩️ Watch the storm (${timeLabel}) live on HailCast radar:`);

    if (mode === 'alert' && nearestStorm) {
      const cell = nearestStorm.cell;
      const lastObs = inputFiles[inputFiles.length - 1];
      const when = lastObs?.target_time ? t(`Observación ${lastObs.target_time}`, `Observed ${lastObs.target_time}`) : timeLabel;
      const where = await describePlace(cell.lat, cell.lon);
      const dist = t(` a ${nearestStorm.distance.toFixed(0)} km de mi ubicación`, ` ${nearestStorm.distance.toFixed(0)} km from my location`);
      textStr = t(
        `⚠️ ALERTA: tormenta severa (${cell.max_dbz.toFixed(0)} dBZ, ${cell.type ? cell.type.toLowerCase() : 'posible granizo'}) ${where}${dist} (${when}). Mirá el radar en vivo y activá las alertas gratis para tu zona:`,
        `⚠️ ALERT: severe storm (${cell.max_dbz.toFixed(0)} dBZ, possible hail) ${where}${dist} (${when}). Watch the live radar and enable free alerts for your area:`
      );
    }

    // Imagen a adjuntar: GIF de todos los cuadros, o captura del cuadro actual
    let fileToShare: File | null = null;
    if (format === 'gif') {
      fileToShare = await buildShareGif();
    }
    try {
      const map = mapRef.current?.getMap();
      if (map && !fileToShare) {
        // Force a render to ensure WebGL buffer is ready and populated
        const blob = await new Promise<Blob | null>((resolve) => {
          map.once('render', () => {
            map.getCanvas().toBlob(resolve, "image/jpeg", 0.8);
          });
          map.triggerRepaint();
        });
        if (blob) {
          fileToShare = new File([blob], "hailcast-radar.jpg", { type: "image/jpeg" });
        }
      }
    } catch (err) {
      console.warn("Failed to capture map screenshot:", err);
    }

    if (navigator.share) {
      try {
        const shareData: ShareData = {
          title: t('Alerta de Tormenta - HailCast', 'Storm Alert - HailCast'),
          text: textStr,
          url: urlStr,
        };

        // Incluir la imagen si es soportado por el navegador
        if (fileToShare && navigator.canShare && navigator.canShare({ files: [fileToShare] })) {
          shareData.files = [fileToShare];
        }

        await navigator.share(shareData);
        return;
      } catch (err: any) {
        if (err.name !== 'AbortError') {
          console.warn('Error sharing via Web Share API:', err);
        } else {
          return; // Usuario canceló la acción
        }
      }
    }

    // Fallback to WhatsApp Web/App
    const whatsappUrl = `https://api.whatsapp.com/send?text=${encodeURIComponent(textStr + " " + urlStr)}`;
    window.open(whatsappUrl, '_blank');
  };

  return (
    <div className="relative w-full h-full bg-black">
      {/* LCP Optimization: Preload current image */}
      {currentImage && currentImage.url && (
        <link rel="preload" as="image" href={currentImage.url} />
      )}

      {/* Dynamic CSS for MapLibre Geolocate Dot and Storm Cell Markers */}
      <style dangerouslySetInnerHTML={{
        __html: `
        :root {
          --dot-scale: ${Math.max(0.5, Math.min(2.0, (zoomLevel - 5) / 5))};
        /* Scale storm cell markers based on zoom to avoid cluttering */
        .storm-cell-marker {
          transform: scale(${Math.max(0.4, Math.min(1.5, (zoomLevel - 5) / 4))});
          transition: transform 0.15s ease-out;
        }
        .storm-cell-marker:hover {
          transform: scale(${Math.max(0.4, Math.min(1.5, (zoomLevel - 5) / 4)) * 1.5}) !important;
          z-index: 50;
        }
      `}} />

      <Map
        ref={mapRef}
        initialViewState={INITIAL_VIEW_STATE}
        style={{ width: '100%', height: '100%' }}
        mapStyle={MAP_STYLE}
        // @ts-expect-error react-map-gl doesn't include preserveDrawingBuffer in type definition
        preserveDrawingBuffer={true}
        attributionControl={false}
        onClick={(event) => {
          const feature = event.features?.[0];
          if (feature && feature.layer.id === 'reports-layer') {
            // Merge with latest fresh data from reports array to prevent stale state from MapLibre cluster/cache
            const latestData = reports?.find(r => r.id === feature.properties?.id);
            const mergedProps = {
              ...feature.properties,
              ...(latestData ? {
                likes_count: latestData.likes_count,
                user_liked: latestData.user_liked
              } : {})
            };

            // Prevent map click from closing immediately if we just clicked a feature
            setSelectedReport({
              longitude: event.lngLat.lng,
              latitude: event.lngLat.lat,
              properties: mergedProps
            });
            setSelectedCell(null);
          } else {
            setSelectedReport(null);
            setSelectedCell(null);
          }
        }}
        onZoom={() => {
          if (mapRef.current) setZoomLevel(mapRef.current.getZoom());
        }}
        onLoad={() => {
          if (!mapRef.current) return;
          setZoomLevel(mapRef.current.getZoom());
          const map = mapRef.current.getMap();
          ensureLightningIcons(map);
          map.on('styleimagemissing', (e: any) => {
            if (LIGHTNING_ICONS.some((i) => i.id === e.id)) ensureLightningIcons(map);
          });
        }}
        interactiveLayerIds={['reports-layer']}
      >
        <NavigationControl position="top-right" style={{ marginTop: '100px', marginRight: '10px' }} />
        <GeolocateControl
          position="top-right"
          style={{ marginRight: '10px' }}
          trackUserLocation={true}
          showUserLocation={true}
        />
        <ScaleControl />
        <FullscreenControl position="top-right" />

        {/* Location Hint Overlay */}
        {showLocationHint && !userLocation && (
          <div
            className="absolute top-[210px] right-[50px] z-50 animate-bounce cursor-pointer flex items-center gap-2"
            onClick={() => setShowLocationHint(false)}
          >
            <div className="bg-primary text-primary-foreground text-xs font-bold px-3 py-1.5 rounded-full shadow-[0_0_15px_rgba(255,255,255,0.3)] flex items-center gap-2 border border-white/20">
              <span>{t("¿Activar tu ubicación?", "Enable your location?")}</span>
              <MapPin className="h-3 w-3 animate-pulse" />
            </div>
            {/* Arrow pointing right towards the geolocation button */}
            <div className="w-0 h-0 border-t-[6px] border-t-transparent border-l-[8px] border-l-primary border-b-[6px] border-b-transparent drop-shadow-sm"></div>
          </div>
        )}



        {/* Satellite / Layers toggle — icon button below zoom controls (top-right) */}
        <div className="absolute top-[285px] right-2 z-50">
          <button
            onClick={() => setSatelliteMode(m => m === 'off' ? 'hail' : m === 'hail' ? 'visible' : m === 'visible' ? 'ir' : 'off')}
            title={satelliteMode === 'off' ? t('Manga de Granizo', 'Hail Swath') : satelliteMode === 'hail' ? t('Satélite visible', 'Visible satellite') : satelliteMode === 'visible' ? t('Satélite IR', 'IR satellite') : t('Limpiar capas', 'Clear layers')}
            className={`relative flex items-center justify-center w-8 h-8 rounded shadow-lg border transition-all
              ${satelliteMode === 'off'
                ? 'bg-white text-gray-700 border-gray-300 hover:bg-gray-100'
                : satelliteMode === 'hail'
                  ? 'bg-red-800 border-red-600 text-white shadow-red-500/40' // Dark Red for hail
                  : satelliteMode === 'visible'
                    ? 'bg-sky-500 border-sky-300 text-white shadow-sky-500/40'
                    : 'bg-indigo-600 border-indigo-400 text-white shadow-indigo-500/40'
              }`}
          >
            <Layers className="h-4 w-4" />
            {satelliteMode !== 'off' && (
              <span className="absolute -bottom-1 -right-1 text-[8px] font-bold leading-none px-[3px] py-[1px] rounded bg-black/70 text-white">
                {satelliteMode === 'visible' ? 'VIS' : satelliteMode === 'ir' ? 'IR' : 'GRZ'}
              </span>
            )}
          </button>

          {/* Satellite Estimated Timestamp Badge */}
          {(satelliteMode === 'visible' || satelliteMode === 'ir') && satelliteEstimTime && (
            <div className="absolute top-[36px] right-0 bg-black/70 text-white text-[10px] px-1.5 py-0.5 rounded border border-white/20 whitespace-nowrap shadow-md">
              {satelliteEstimTime}
            </div>
          )}
        </div>

        {/* Selector de radar: solo con más de un radar activo (Sur / Norte) */}
        {radars.length > 1 && (
          <div className="absolute top-[465px] right-2 z-50">
            <button
              onClick={(e) => {
                e.stopPropagation();
                const idx = radars.findIndex((r) => r.id === radar);
                onRadarChange?.(radars[(idx + 1) % radars.length].id);
              }}
              title={radarInfo ? radarInfo.label : t('Cambiar de radar', 'Switch radar')}
              className={`relative flex items-center justify-center w-8 h-8 rounded shadow-lg border transition-all text-white ${
                radar === DEFAULT_RADAR
                  ? 'bg-slate-700 border-slate-500 hover:bg-slate-600 shadow-slate-500/40'
                  : 'bg-amber-600 border-amber-400 hover:bg-amber-500 shadow-amber-500/40'
              }`}
            >
              <RadioTower className="h-4 w-4" />
              <span className="absolute -bottom-1 -right-1 text-[8px] font-bold leading-none px-[3px] py-[1px] rounded bg-black/70 text-white uppercase">
                {radar === DEFAULT_RADAR ? 'S' : radar.charAt(0)}
              </span>
            </button>
          </div>
        )}

        {/* WhatsApp Share Button: abre un menú con dos formatos (GIF animado o captura) */}
        <div className="absolute top-[345px] right-2 z-50 flex items-center gap-2">
          {shareMenuOpen && !shareProgress && (
            <div className="flex items-center gap-1.5 bg-black/85 border border-white/20 rounded-lg p-1 shadow-xl">
              <button
                onClick={(e) => { e.stopPropagation(); handleShare(hasActiveStorm ? 'alert' : 'radar', 'gif'); }}
                disabled={frames.length === 0}
                title={t('GIF con la animación completa (observado + predicción)', 'GIF with the full animation (observed + forecast)')}
                className="flex items-center gap-1 px-2 h-7 rounded text-[11px] font-bold text-white bg-[#25D366] hover:bg-[#128C7E] disabled:opacity-40"
              >
                <Film className="h-3.5 w-3.5" /> GIF
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); handleShare(hasActiveStorm ? 'alert' : 'radar', 'image'); }}
                title={t('Captura del cuadro actual', 'Snapshot of the current frame')}
                className="flex items-center gap-1 px-2 h-7 rounded text-[11px] font-bold text-white bg-zinc-700 hover:bg-zinc-600"
              >
                <Camera className="h-3.5 w-3.5" /> {t('Captura', 'Snapshot')}
              </button>
            </div>
          )}
          <button
            onClick={(e) => { e.stopPropagation(); if (!shareProgress) setShareMenuOpen((v) => !v); }}
            disabled={!!shareProgress}
            title={hasActiveStorm
              ? t("Compartir alerta de tormenta por WhatsApp", "Share storm alert via WhatsApp")
              : t("Compartir radar por WhatsApp", "Share radar via WhatsApp")}
            className={`flex items-center justify-center w-8 h-8 rounded shadow-lg border transition-all text-white ${shareProgress
              ? "bg-zinc-800 border-zinc-600 text-[10px] font-bold"
              : hasActiveStorm
                ? "bg-red-600 border-red-400 hover:bg-red-500 shadow-red-500/40 animate-pulse"
                : "bg-[#25D366] border-[#128C7E] hover:bg-[#128C7E] shadow-[#25D366]/40"}`}
          >
            {shareProgress ? `${Math.round((shareProgress.done / shareProgress.total) * 100)}%` : <Share2 className="h-4 w-4" />}
          </button>
        </div>

        {/* Contact Button (below share) */}
        {onContactClick && (
          <div className="absolute top-[385px] right-2 z-50">
            <button
              onClick={(e) => { e.stopPropagation(); onContactClick(); }}
              title={t("Contacto", "Contact")}
              className="flex items-center justify-center w-8 h-8 rounded shadow-lg border transition-all bg-white/90 border-white/60 text-sky-700 hover:bg-white hover:text-sky-600"
            >
              <Mail className="h-4 w-4" />
            </button>
          </div>
        )}

        {/* Nearest Storm Locator Button */}
        {nearestStorm && (
          <div className="absolute top-[425px] right-2 z-50">
            <button
              onClick={(e) => {
                e.stopPropagation();
                setShowNearestStormMarker(true);
                mapRef.current?.flyTo({
                  center: [nearestStorm.cell.lon, nearestStorm.cell.lat],
                  zoom: 9,
                  duration: 1500
                });
                toast({
                  title: t("Celda más cercana", "Nearest Cell"),
                  description: t(`Centro convectivo (${nearestStorm.cell.max_dbz.toFixed(0)}dBZ) a ${nearestStorm.distance.toFixed(1)} km`, `Convective core (${nearestStorm.cell.max_dbz.toFixed(0)}dBZ) at ${nearestStorm.distance.toFixed(1)} km`),
                  duration: 5000,
                  className: "bg-white/10 backdrop-blur-md border-white/20 text-white"
                });
              }}
              title={t("Localizar tormenta más cercana (>50dBZ)", "Locate nearest storm (>50dBZ)")}
              className="flex items-center justify-center w-8 h-8 rounded shadow-lg border transition-all bg-red-600 border-red-400 text-white hover:bg-red-500 shadow-red-500/40 relative"
            >
              <Zap className="h-4 w-4 fill-white animate-pulse" />
              {/* Optional distance mini-badge over the button 
              <span className="absolute -bottom-2 -left-2 text-[8px] font-bold px-1 rounded bg-black/80 text-white border border-white/20">
                {nearestStorm.distance.toFixed(0)}k
              </span> */}
            </button>
          </div>
        )}

        {
          boundariesData && (
            <Source id="boundaries-source" type="geojson" data={boundariesData}>
              <Layer {...boundaryLayerStyle} />
            </Source>
          )
        }

        {/* Aircraft Trail Layer — per-callsign color via data-driven MapLibre match */}
        <Source id="aircraft-trail-source" type="geojson" data={trailGeoJSON}>
          <Layer
            id="aircraft-trail-layer"
            type="line"
            paint={{
              'line-color': [
                'match', ['get', 'callsign'],
                'VBCR', '#ffa500',
                'LV-BCR', '#ffa500',
                'VBCT', '#00ffff',
                'LV-BCT', '#00ffff',
                'VBCU', '#00ff00',
                'LV-BCU', '#00ff00',
                'LQ-BCP', '#00ff00',
                'LQ-BCU', '#00ff00',
                '#ffffff'
              ] as any,
              'line-width': [
                'interpolate',
                ['linear'],
                ['zoom'],
                6, 1.5,
                11, 4
              ],
              'line-opacity': 0.7
            }}
          />
          <Layer
            id="aircraft-trail-nodes"
            type="circle"
            paint={{
              'circle-color': [
                'match', ['get', 'callsign'],
                'VBCR', '#ffa500',
                'LV-BCR', '#ffa500',
                'VBCT', '#00ffff',
                'LV-BCT', '#00ffff',
                'VBCU', '#00ff00',
                'LV-BCU', '#00ff00',
                'LQ-BCP', '#00ff00',
                'LQ-BCU', '#00ff00',
                '#ffffff'
              ] as any,
              'circle-radius': [
                'interpolate',
                ['linear'],
                ['zoom'],
                6, 0.5,
                11, 2
              ],
              'circle-opacity': 0.8
            }}
          />
        </Source>

        {/* GOES-East Satellite Layer — single WMS image cropped to Mendoza province */}
        {(satelliteMode === 'visible' || satelliteMode === 'ir') && (() => {
          const layerName = satelliteMode === 'visible'
            ? 'GOES-East_ABI_GeoColor'
            : 'GOES-East_ABI_Band13_Clean_Infrared';

          // Mendoza province bbox (EPSG:4326): lon_min, lat_min, lon_max, lat_max
          // Fetching a SINGLE image instead of dozens of tiles → much faster load
          const MZA_BBOX = '-70.6,-37.6,-66.3,-31.9';       // WGS84 corners
          const wmsUrl = [
            'https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi?',
            'SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap',
            '&FORMAT=image%2Fpng&TRANSPARENT=true',
            `&LAYERS=${layerName}`,
            '&SRS=EPSG%3A4326&STYLES=',
            `&BBOX=${MZA_BBOX}`,
            '&WIDTH=800&HEIGHT=600',
            '&EXCEPTIONS=INIMAGE',
          ].join('');

          // Four corners of Mendoza bbox as [lon, lat] for MapLibre Source type="image"
          const coordinates: [[number, number], [number, number], [number, number], [number, number]] = [
            [-70.6, -31.9], // top-left
            [-66.3, -31.9], // top-right
            [-66.3, -37.6], // bottom-right
            [-70.6, -37.6], // bottom-left
          ];

          return (
            <Source
              key={`satellite-${satelliteMode}`}
              id="satellite-source"
              type="image"
              url={wmsUrl}
              coordinates={coordinates}
            >
              <Layer
                id="satellite-layer"
                type="raster"
                paint={{
                  'raster-opacity': 0.7,
                  'raster-resampling': 'linear',
                }}
                beforeId="aircraft-trail-layer"
              />
            </Source>
          );
        })()}

        {/* Daily Hail Swath (> 55dBZ history) */}
        {satelliteMode === 'hail' && hailSwathData && (
          <Source id="hail-swath-source" type="geojson" data={hailSwathData}>
            <Layer
              id="hail-swath-layer"
              type="circle"
              paint={{
                'circle-color': '#8B0000', // Solid Dark Red
                'circle-radius': [
                  'interpolate',
                  ['linear'],
                  ['zoom'],
                  7, 2,
                  12, 6
                ],
                'circle-opacity': 0.85 // High opacity, no blur for solid look
              }}
            />
          </Source>
        )}

        {
          currentImage && imageCoordinates && !previewOverlay && (
            <Source
              id="radar-source"
              type="image"
              url={showSmoothRadar ? currentImage.url.replace('.png', '_smoothed.png') : currentImage.url}
              coordinates={imageCoordinates}
            >
              <Layer
                id="radar-layer"
                type="raster"
                paint={{
                  "raster-opacity": 0.8,
                  "raster-fade-duration": 0
                }}
                beforeId="aircraft-trail-layer"
              />
            </Source>
          )
        }

        {/* Preview Overlay (Story Mode exploration) */}
        {
          previewOverlay && previewOverlay.bounds && (() => {
            const b = previewOverlay.bounds;
            const minLat = Math.min(b[0][0], b[1][0]);
            const maxLat = Math.max(b[0][0], b[1][0]);
            const minLon = Math.min(b[0][1], b[1][1]);
            const maxLon = Math.max(b[0][1], b[1][1]);
            const coords: [[number, number], [number, number], [number, number], [number, number]] = [
              [minLon, maxLat], [maxLon, maxLat], [maxLon, minLat], [minLon, minLat]
            ];
            return (
              <Source
                id="preview-source"
                type="image"
                url={previewOverlay.url}
                coordinates={coords}
              >
                <Layer
                  id="preview-layer"
                  type="raster"
                  paint={{ "raster-opacity": 0.85, "raster-fade-duration": 0 }}
                  beforeId="aircraft-trail-layer"
                />
              </Source>
            );
          })()
        }

        {/* Reports Layer (Crowdsourcing) */}
        {
          reportsGeoJSON && (
            <Source id="reports-source" type="geojson" data={reportsGeoJSON as any}>
              <Layer {...reportLayerStyle as any} />
              {/* Labels disabled for cleaner look, hover tooltip could be better */}
            </Source>
          )
        }

        {/* Nearest Storm Marker (White Dot) */}
        {showNearestStormMarker && nearestStorm && (
          <Marker
            longitude={nearestStorm.cell.lon}
            latitude={nearestStorm.cell.lat}
            anchor="center"
          >
            <div className="relative flex items-center justify-center pointer-events-none">
              <div className="absolute w-6 h-6 bg-white/30 rounded-full animate-ping z-0" />
              <div className="w-3 h-3 bg-white rounded-full border-2 border-black shadow-[0_0_10px_rgba(255,255,255,0.8)] z-10" />
            </div>
          </Marker>
        )}

        {/* Storm Cells Identification Markers */}
        {showStormCells && currentImage?.cells?.map((cell, idx) => {
          const dbz = cell.max_dbz;
          let color = '#3b82f6'; // Blue (Weak)
          if (dbz >= 60) color = '#9333ea'; // Purple (Severe)
          else if (dbz >= 50) color = '#ef4444'; // Red (Hail)
          else if (dbz >= 40) color = '#f97316'; // Orange (Strong)
          else if (dbz >= 30) color = '#eab308'; // Yellow (Moderate)

          return (
            <Marker
              key={`cell-${currentImage.target_time}-${idx}`}
              longitude={cell.lon}
              latitude={cell.lat}
              anchor="center"
              onClick={(e) => {
                e.originalEvent.stopPropagation();
                setSelectedCell(cell);
                setSelectedReport(null);
              }}
            >
              <div
                className="storm-cell-marker cursor-pointer w-4 h-4 rounded-full border-2 border-white/80 flex items-center justify-center shadow-lg"
                style={{ backgroundColor: color }}
              >
                {/* Inner dot */}
                <div className="w-1.5 h-1.5 bg-white/50 rounded-full" />
              </div>
            </Marker>
          );
        })}

        {/* Storm Cell Popup */}
        {selectedCell && (
          <Popup
            longitude={selectedCell.lon}
            latitude={selectedCell.lat}
            anchor="bottom"
            onClose={() => setSelectedCell(null)}
            closeOnClick={false}
            className="z-50 dark-popup"
            offset={10}
          >
            <div className="p-3 text-zinc-100 min-w-[150px] flex flex-col gap-1 rounded-md">
              <div className="flex items-center gap-2 border-b border-zinc-700/50 pb-2 mb-1">
                <CloudRain className="w-4 h-4 text-blue-400" />
                <span className="font-bold text-sm uppercase tracking-wide">
                  {t('Núcleo Detectado', 'Detected Core')}
                </span>
              </div>

              <div className="flex items-end gap-1 mt-1">
                <span className="text-2xl font-black text-white leading-none">+{selectedCell.max_dbz.toFixed(0)}</span>
                <span className="text-xs text-zinc-400 font-bold mb-0.5">dBZ</span>
              </div>

              <div className="flex items-center justify-between mt-2 pt-2 border-t border-zinc-700/50">
                <div className="text-sm font-medium">
                  {selectedCell.max_dbz >= 60 && (
                    <span className="text-purple-400 drop-shadow-[0_0_5px_rgba(168,85,247,0.5)]">
                      {t('Tormenta Severa', 'Severe Storm')} ⚠️
                    </span>
                  )}
                  {selectedCell.max_dbz >= 50 && selectedCell.max_dbz < 60 && (
                    <span className="text-red-400 drop-shadow-[0_0_5px_rgba(248,113,113,0.5)]">
                      {t('Probable Granizo', 'Probable Hail')} 🧊
                    </span>
                  )}
                  {selectedCell.max_dbz >= 40 && selectedCell.max_dbz < 50 && (
                    <span className="text-orange-400">
                      {t('Lluvia Fuerte', 'Heavy Rain')} 🌧️
                    </span>
                  )}
                  {selectedCell.max_dbz >= 30 && selectedCell.max_dbz < 40 && (
                    <span className="text-yellow-400">
                      {t('Lluvia Moderada', 'Moderate Rain')} 🌦️
                    </span>
                  )}
                  {selectedCell.max_dbz < 30 && (
                    <span className="text-blue-400">
                      {t('Lluvia Débil', 'Light Rain')} 💧
                    </span>
                  )}
                </div>

                {userLocation && (
                  <div className="flex flex-col items-end">
                    <span className="text-[10px] text-zinc-400 uppercase tracking-wider font-bold">{t('Distancia', 'Distance')}</span>
                    <span className="text-sm font-black text-sky-400 shadow-sm">
                      {haversineDistance(userLocation, { lat: selectedCell.lat, lon: selectedCell.lon }).toFixed(1)} km
                    </span>
                  </div>
                )}
              </div>
            </div>
          </Popup>
        )}

        {/* Aircraft Layer (TITAN Telemetry + OpenSky) */}
        {(() => {
          const AC_COLORS: Record<string, string> = {
            'VBCR': '#ffa500',
            'LV-BCR': '#ffa500',
            'VBCT': '#00ffff',
            'LV-BCT': '#00ffff',
            'VBCU': '#00ff00',
            'LV-BCU': '#00ff00',
            'LQ-BCP': '#00ff00',
            'LQ-BCU': '#00ff00',
          };
          const getColor = (cs: string) => AC_COLORS[cs] ?? '#ffffff';

          return aircraftData.map((ac) => {
            const color = getColor(ac.callsign);
            const isSelected = selectedAircraft === ac.callsign;
            return (
              <Marker
                key={ac.callsign}
                longitude={ac.lon}
                latitude={ac.lat}
                anchor="center"
              >
                <div
                  className="relative group cursor-pointer"
                  onClick={(e) => {
                    e.stopPropagation();
                    setSelectedAircraft(isSelected ? null : ac.callsign);
                  }}
                  style={{ transition: 'transform 0.4s ease' }}
                >
                  {/* Airplane SVG — tip points UP = north = 0°. Rotates with heading. */}
                  <div style={{
                    transform: `rotate(${ac.heading ?? 0}deg)`,
                    color,
                    filter: `drop-shadow(0 0 4px ${color}cc)`,
                    transition: 'transform 0.4s ease',
                  }}>
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      viewBox="0 0 24 24"
                      width="16" height="16"
                      fill={color}
                      stroke="rgba(0,0,0,0.6)"
                      strokeWidth="1.5"
                    >
                      <circle cx="12" cy="12" r="6" />
                    </svg>
                  </div>

                  {/* Tooltip: visible on hover (desktop) OR when tapped/selected (mobile) */}
                  <div
                    className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 flex flex-col items-center z-50 pointer-events-none"
                    style={{ display: isSelected ? 'flex' : undefined }}
                  >
                    <div
                      className={`bg-black/80 text-xs rounded px-2 py-1 whitespace-nowrap font-mono
                        ${isSelected ? 'flex flex-col' : 'hidden group-hover:flex flex-col'}`}
                      style={{ border: `1px solid ${color}80`, color: '#e5e7eb' }}
                    >
                      <div className="font-bold" style={{ color }}>{ac.reg}</div>
                      <div>Alt: {ac.altitude ? `${Math.round(ac.altitude)}m` : '--'}</div>
                      <div>Vel: {ac.velocity ? `${Math.round(ac.velocity * 1.94)}kt` : '--'}</div>
                      {ac.source === 'titan' && <div className="text-yellow-400 text-[10px]">● TITAN</div>}
                    </div>
                    <div
                      className={`w-0 h-0 border-l-4 border-r-4 border-t-4 border-l-transparent border-r-transparent
                        ${isSelected ? 'block' : 'hidden group-hover:block'}`}
                      style={{ borderTopColor: `${color}80` }}
                    />
                  </div>
                </div>
              </Marker>
            );
          });
        })()}

        {/* Rayos GLM: puntos coloreados por antigüedad (amarillo nuevo → gris viejo), encima del radar */}
        {showLightning && lightningData && lightningData.features.length > 0 && (
          <Source id="lightning-source" type="geojson" data={lightningData as any}>
            <Layer
              id="lightning-layer"
              type="symbol"
              layout={{
                'icon-image': ['step', ['get', 'age_min'], 'bolt-0', 2, 'bolt-1', 5, 'bolt-2', 8, 'bolt-3'],
                'icon-size': ['interpolate', ['linear'], ['get', 'age_min'], 0, 0.75, 10, 0.45],
                'icon-allow-overlap': true,
                'icon-ignore-placement': true,
              }}
              paint={{
                'icon-opacity': ['interpolate', ['linear'], ['get', 'age_min'], 0, 1, 10, 0.5],
              }}
            />
          </Source>
        )}

        {/* Parques solares (solo superadmin): estado de las alertas privadas */}
        {solarParks.map((park) => {
          const color = park.active ? (park.type === 'GRANIZO' ? '#ef4444' : '#f97316') : '#facc15';
          const isSelected = selectedPark === park.id;
          const fmtTime = (iso?: string | null) => iso
            ? new Date(iso).toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit', timeZone: 'America/Argentina/Mendoza' })
            : '--';
          return (
            <Marker key={`park-${park.id}`} longitude={park.lon} latitude={park.lat} anchor="center">
              <div
                className="relative group cursor-pointer"
                onClick={(e) => { e.stopPropagation(); setSelectedPark(isSelected ? null : park.id); }}
              >
                <div
                  className={`flex items-center justify-center rounded-md border ${park.active ? 'animate-pulse' : ''}`}
                  style={{ width: 18, height: 18, background: `${color}33`, borderColor: color, boxShadow: `0 0 6px ${color}99` }}
                >
                  <Sun className="h-3 w-3" style={{ color }} />
                </div>
                <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 flex flex-col items-center z-50 pointer-events-none">
                  <div
                    className={`bg-black/85 text-xs rounded px-2 py-1 whitespace-nowrap font-mono ${isSelected ? 'flex flex-col' : 'hidden group-hover:flex flex-col'}`}
                    style={{ border: `1px solid ${color}80`, color: '#e5e7eb' }}
                  >
                    <div className="font-bold" style={{ color }}>☀ {park.name}</div>
                    <div>{park.active ? `ALERTA ${park.type} desde ${fmtTime(park.since)}` : 'Sin alerta'}</div>
                    <div>
                      Celda más cercana: {park.nearest_km != null ? `${park.nearest_km} km` : '--'}
                      {park.nearest_dbz != null ? ` · ${park.nearest_dbz} dBZ` : ''}
                    </div>
                    {park.last_sent && <div className="text-gray-400">Último aviso: {fmtTime(park.last_sent)}</div>}
                  </div>
                  <div
                    className={`w-0 h-0 border-l-4 border-r-4 border-t-4 border-l-transparent border-r-transparent ${isSelected ? 'block' : 'hidden group-hover:block'}`}
                    style={{ borderTopColor: `${color}80` }}
                  />
                </div>
              </div>
            </Marker>
          );
        })}

        {/* Weather Stations Layer */}
        {showStations && stationsData && stationsData.features && stationsData.features.map((station: any) => {
          const isSelected = selectedStation?.estacion === station.properties.estacion;
          const { lat, lng } = station.properties;

          if (!lat || !lng) return null;

          return (
            <Marker
              key={`station-${station.properties.estacion}`}
              longitude={lng}
              latitude={lat}
              anchor="center"
              onClick={(e) => {
                e.originalEvent.stopPropagation();
                setSelectedStation(isSelected ? null : station.properties);
              }}
            >
              <div
                className={`flex items-center justify-center rounded-full border border-sky-400/50 bg-sky-900/60 shadow-lg backdrop-blur-md cursor-pointer transition-all hover:bg-sky-500/80 hover:scale-110 ${isSelected ? 'ring-2 ring-sky-300 scale-110' : ''}`}
                style={{ width: '24px', height: '24px' }}
                title={station.properties.Nombre}
              >
                <Thermometer className="h-3.5 w-3.5 text-white" />
              </div>
            </Marker>
          );
        })}

        {/* Station Popup */}
        {selectedStation && selectedStation.lat && selectedStation.lng && (
          <Popup
            longitude={Number(selectedStation.lng)}
            latitude={Number(selectedStation.lat)}
            anchor="bottom"
            onClose={() => setSelectedStation(null)}
            closeOnClick={false}
            className="z-50 dark-popup"
          >
            <div className="p-2 min-w-[200px] text-zinc-100">
              <h3 className="font-bold text-sm uppercase mb-2 text-sky-400 flex items-center gap-1 border-b border-zinc-800 pb-1">
                <Thermometer className="w-4 h-4" />
                {selectedStation.Nombre}
              </h3>

              <div className="grid grid-cols-2 gap-y-2 gap-x-4 text-xs mt-2">
                <div className="flex flex-col">
                  <span className="text-zinc-500 font-bold uppercase tracking-wider text-[10px]">{t('Temp. Aire', 'Air Temp.')}</span>
                  <span className="text-white text-lg font-black">{selectedStation.tempAire}°C</span>
                </div>

                <div className="flex flex-col">
                  <span className="text-zinc-500 font-bold uppercase tracking-wider text-[10px]">{t('Humedad', 'Humidity')}</span>
                  <span className="text-white text-lg font-black">{selectedStation.humedad}%</span>
                </div>

                <div className="flex flex-col">
                  <span className="text-zinc-500 font-bold uppercase tracking-wider text-[10px]">{t('Viento', 'Wind')}</span>
                  <span className="text-white">{selectedStation.velocidadViento} km/h - {selectedStation.direccionVientoTexto}</span>
                </div>

                <div className="flex flex-col pt-2 col-span-2 border-t border-zinc-800/50">
                  <span className="text-zinc-500 font-bold uppercase tracking-wider text-[10px]">{t('Última actualización', 'Last update')}</span>
                  <span className="text-zinc-300">{selectedStation.fecha}</span>
                </div>
              </div>
            </div>
          </Popup>
        )}

        {/* Report Popup */}
        {
          selectedReport && (
            <Popup
              longitude={selectedReport.longitude}
              latitude={selectedReport.latitude}
              anchor="bottom"
              onClose={() => setSelectedReport(null)}
              closeOnClick={false}
              className="z-50 dark-popup"
            >
              <div className="p-2 min-w-[200px] max-w-[250px] text-zinc-100">
                {selectedReport.properties.image_url && (
                  <div className="mb-2 rounded-md overflow-hidden h-32 w-full bg-zinc-800 relative">
                    <img
                      src={`${process.env.NEXT_PUBLIC_API_URL || ''}${selectedReport.properties.image_url}`}
                      alt="Reporte"
                      className="w-full h-full object-cover"
                      onError={(e) => {
                        // Fallback if image fails
                        (e.target as HTMLImageElement).style.display = 'none';
                      }}
                    />
                  </div>
                )}
                <div className="flex justify-between items-start pr-6">
                  <h3 className="font-bold text-sm uppercase mb-1 text-zinc-100">{selectedReport.properties.type.replace('_', ' ')}</h3>
                  <div className="flex gap-1 items-center">

                    {/* LIKE BUTTON */}
                    <button
                      onClick={async (e) => {
                        e.stopPropagation();
                        if (!token) {
                          alert(t("Debes iniciar sesión para dar me gusta.", "You must be logged in to like."));
                          return;
                        }
                        // Optimistic update
                        const isLiked = selectedReport.properties.user_liked;
                        const newCount = isLiked
                          ? Math.max(0, (selectedReport.properties.likes_count || 0) - 1)
                          : (selectedReport.properties.likes_count || 0) + 1;

                        const prev = JSON.stringify(selectedReport);
                        setSelectedReport((current: any) => current ? {
                          ...current,
                          properties: {
                            ...current.properties,
                            user_liked: !isLiked,
                            likes_count: newCount
                          }
                        } : null);

                        try {
                          await toggleReportLike(selectedReport.properties.id, token);
                          if (onReportUpdate) onReportUpdate();
                        } catch (e) {
                          setSelectedReport(JSON.parse(prev));
                          alert(t("Error al dar me gusta", "Error toggling like"));
                        }
                      }}
                      className={`flex items-center gap-1 p-1 text-xs rounded transition-colors ${selectedReport.properties.user_liked ? 'text-red-500 hover:text-red-400' : 'text-zinc-400 hover:text-red-400'}`}
                      title={selectedReport.properties.user_liked ? t("Quitar me gusta", "Unlike") : t("Me gusta", "Like")}
                    >
                      <Heart className="w-4 h-4" fill={selectedReport.properties.user_liked ? "currentColor" : "none"} />
                      <span>{selectedReport.properties.likes_count || 0}</span>
                    </button>

                    {user?.username === selectedReport.properties.username && (
                      <button
                        onClick={() => {
                          const reportToEdit: WeatherReport = {
                            id: selectedReport.properties.id,
                            report_type: selectedReport.properties.type,
                            description: selectedReport.properties.description,
                            image_url: selectedReport.properties.image_url,
                            latitude: selectedReport.latitude,
                            longitude: selectedReport.longitude,
                            username: selectedReport.properties.username
                          };
                          setEditingReport(reportToEdit);
                          setIsReportOpen(true);
                          setSelectedReport(null); // Close popup
                        }}
                        className="text-zinc-400 hover:text-white p-1 ml-1"
                        title={t("Editar Reporte", "Edit Report")}
                      >
                        <Pencil className="w-4 h-4" />
                      </button>
                    )}
                    {user?.role === 'admin' && (
                      <button
                        onClick={async () => {
                          if (confirm(t("¿Eliminar este reporte permanentemente?", "Permanently delete this report?"))) {
                            try {
                              await deleteReport(selectedReport.properties.id, token!);
                              if (onReportUpdate) onReportUpdate();
                              setSelectedReport(null);
                            } catch (e) {
                              alert(t("Error al eliminar", "Error deleting"));
                            }
                          }
                        }}
                        className="text-red-500 hover:text-red-400 p-1"
                        title={t("Eliminar Reporte (Admin)", "Delete Report (Admin)")}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    )}
                  </div>
                </div>
                <p className="text-xs text-zinc-400 mb-2">{selectedReport.properties.time} - {selectedReport.properties.username}</p>
                {selectedReport.properties.description && (
                  <p className="text-sm border-t border-zinc-800 pt-2 mt-1 text-zinc-300">{selectedReport.properties.description}</p>
                )}
              </div>
            </Popup>
          )
        }

        {districtsData && (
          <Source id="districts" type="geojson" data={districtsData}>
            <Layer {...districtLineStyle} />
            <Layer {...districtLabelStyle} />
          </Source>
        )}

        {/* Unified Timeline Control Bar */}
        <div className="absolute bottom-0 left-0 right-0 p-4 bg-gradient-to-t from-black/90 via-black/60 to-transparent pb-8 pt-12">
          <div className="max-w-4xl mx-auto w-full flex flex-col gap-2">

            {/* Time Label & Status */}
            <div className="flex justify-between items-end px-2 mb-1">
              <div className="flex flex-col">
                <span className={`text-xs font-bold uppercase tracking-wider ${isPrediction ? 'text-primary' : 'text-muted-foreground'}`}>
                  {isPrediction ? t('Modelo Predictivo', 'Predictive Model') : t('Datos Observados', 'Observed Data')}
                </span>
                <span className="text-2xl font-bold text-white drop-shadow-md flex items-center gap-2 tracking-wide">
                  {isPrediction ? <Clock className="w-6 h-6 text-white" /> : <Calendar className="w-6 h-6 text-white/80" />}
                  {getTimeLabel()}
                </span>
              </div>

              <div className="flex gap-2">
                <Button
                  size="icon"
                  variant="secondary"
                  className="h-10 w-10 rounded-full bg-green-400 text-black hover:bg-green-500 shadow-[0_0_15px_rgba(74,222,128,0.5)]"
                  onClick={togglePlay}
                >
                  {isPlaying ? <Pause className="h-5 w-5" /> : <Play className="h-5 w-5 ml-1" />}
                </Button>
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-10 w-10 rounded-full text-muted-foreground hover:text-foreground hover:bg-white/10"
                  onClick={resetAnimation}
                >
                  <RotateCcw className="h-5 w-5" />
                </Button>
              </div>
            </div>

            {/* Slider Track Container */}
            <div className="relative h-10 flex flex-col justify-center group">

              {/* Custom Track Background (Yellow / Blue Split) */}
              <div className="absolute top-1/2 left-0 right-0 h-1.5 -translate-y-1/2 rounded-full overflow-hidden flex pointer-events-none">
                <div
                  className="h-full bg-yellow-500/20"
                  style={{ width: '22%' }}
                />
                <div
                  className="h-full bg-blue-500/20"
                  style={{ width: '78%' }}
                />
              </div>

              {/* Visual Tick for "Now" */}
              <div
                className="absolute top-1/2 h-3 w-0.5 bg-white/50 -translate-y-1/2 z-0"
                style={{ left: '22%' }}
              />

              {/* The Slider Component */}
              <Slider
                value={[sliderDragValue ?? currentFrameIndex]}
                min={0}
                max={Math.max(0, totalFrames - 1)}
                step={0.01}
                onValueChange={(value) => {
                  // Move thumb smoothly; only switch frame when crossing an integer
                  setIsPlaying(false);
                  setSliderDragValue(value[0]);
                  const rounded = Math.round(value[0]);
                  if (rounded !== currentFrameIndex) setCurrentFrameIndex(rounded);
                }}
                onValueCommit={(value) => {
                  // Commit exact frame on pointer/touch release
                  const rounded = Math.round(value[0]);
                  setCurrentFrameIndex(rounded);
                  setSliderDragValue(null);
                }}
                className={`cursor-pointer z-10 ${isPrediction ? '[&_.bg-primary]:bg-blue-500 [&_.border-primary]:border-blue-500' : '[&_.bg-primary]:bg-yellow-500 [&_.border-primary]:border-yellow-500'}`}
              />

              {/* Labels */}
              <div className="absolute top-full left-0 right-0 mt-1 h-5 text-[10px] sm:text-xs md:text-sm font-sans uppercase tracking-wider text-muted-foreground">
                {/* Past Label */}
                <span className="absolute left-0 text-yellow-500 font-semibold">{t("Pasado", "Past")}</span>

                {/* Now Label (Centered at split) */}
                <span
                  className="absolute -translate-x-1/2 text-white font-bold"
                  style={{ left: '22%' }}
                >
                  {t("ACTUAL", "NOW")}
                </span>

                {/* Future Label */}
                <span className="absolute right-0 text-blue-500 font-semibold">{t("Futuro", "Future")}</span>
              </div>
            </div>

          </div>
        </div>
      </Map >

      {/* Report Dialog */}
      <ReportDialog
        open={isReportOpen}
        onOpenChange={(open) => {
          setIsReportOpen(open);
          if (!open) setEditingReport(null);
        }}
        userLocation={userLocation ?? null}
        existingReport={editingReport}
      />
    </div >
  )
})
