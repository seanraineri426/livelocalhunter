import { useEffect, useMemo, useRef } from 'react'
import mapboxgl from 'mapbox-gl'
import 'mapbox-gl/dist/mapbox-gl.css'
import { formatAddress, markerColor } from '../lib/format'

const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN
const SOUTH_FLORIDA_CENTER = [-80.29, 26.02]

function getCentroid(parcel) {
  const centroid = parcel?.centroid
  const lat = Number(centroid?.lat)
  const lon = Number(centroid?.lon)
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null
  return { lat, lon }
}

export function ParcelMap({ context, tone, loading }) {
  const containerRef = useRef(null)
  const mapRef = useRef(null)
  const markerRef = useRef(null)
  const centroid = useMemo(() => getCentroid(context?.parcel), [context])

  useEffect(() => {
    if (!MAPBOX_TOKEN || !containerRef.current || mapRef.current) return

    mapboxgl.accessToken = MAPBOX_TOKEN
    mapRef.current = new mapboxgl.Map({
      container: containerRef.current,
      style: 'mapbox://styles/mapbox/dark-v11',
      center: SOUTH_FLORIDA_CENTER,
      zoom: 8.4,
      pitch: 48,
      bearing: -12,
      antialias: true,
    })
    mapRef.current.addControl(new mapboxgl.NavigationControl({ showCompass: true }), 'bottom-right')

    return () => {
      markerRef.current?.remove()
      mapRef.current?.remove()
      markerRef.current = null
      mapRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!mapRef.current || !centroid) return

    const color = markerColor(tone)
    markerRef.current?.remove()
    markerRef.current = new mapboxgl.Marker({ color, scale: 1.05 })
      .setLngLat([centroid.lon, centroid.lat])
      .setPopup(
        new mapboxgl.Popup({ offset: 24 }).setHTML(
          `<strong>${context.parcel.source_parcel_id || 'Parcel'}</strong><br/>${formatAddress(context.parcel) || 'Address not stored'}`,
        ),
      )
      .addTo(mapRef.current)

    mapRef.current.flyTo({
      center: [centroid.lon, centroid.lat],
      zoom: 15.4,
      pitch: 54,
      bearing: -16,
      speed: 0.85,
      curve: 1.2,
      essential: true,
    })
  }, [centroid, context, tone])

  if (!MAPBOX_TOKEN) {
    return (
      <section className="map-shell map-fallback">
        <div>
          <p className="eyebrow">Mapbox unavailable</p>
          <h2>Add `VITE_MAPBOX_TOKEN` to load the parcel map.</h2>
          <p className="muted">
            The API and parcel workspace still work. For local Vite, map the existing server-side
            `MAPBOX_TOKEN` value to `VITE_MAPBOX_TOKEN` without exposing any OpenRouter or database keys.
          </p>
        </div>
      </section>
    )
  }

  return (
    <section className="map-shell">
      <div className="map-canvas" ref={containerRef} />
      <div className="map-overlay top">
        <span className={`status-dot ${tone}`} />
        <div>
          <strong>{context?.parcel?.source_parcel_id || 'Select a parcel'}</strong>
          <span>{centroid ? `${centroid.lat.toFixed(5)}, ${centroid.lon.toFixed(5)}` : 'Awaiting centroid'}</span>
        </div>
      </div>
      <div className="map-overlay bottom">
        <span>{loading === 'context' ? 'Loading parcel context...' : 'Marker MVP: parcel centroid'}</span>
        <span>Polygons/vector tiles deferred</span>
      </div>
    </section>
  )
}
