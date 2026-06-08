import { useEffect, useMemo, useRef } from 'react'
import mapboxgl from 'mapbox-gl'
import 'mapbox-gl/dist/mapbox-gl.css'
import { formatAddress, markerColor } from '../lib/format'

const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN
const SOUTH_FLORIDA_CENTER = [-80.29, 26.02]
const PARCEL_SOURCE_ID = 'selected-parcel'
const PARCEL_FILL_LAYER_ID = 'selected-parcel-fill'
const PARCEL_OUTLINE_LAYER_ID = 'selected-parcel-outline'
const FLAT_CAMERA = { pitch: 0, bearing: 0 }

function getCentroid(parcel) {
  const centroid = parcel?.centroid
  const lat = Number(centroid?.lat)
  const lon = Number(centroid?.lon)
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null
  return { lat, lon }
}

function ensureParcelLayers(map) {
  if (!map.getSource(PARCEL_SOURCE_ID)) {
    map.addSource(PARCEL_SOURCE_ID, {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] },
    })
  }

  if (!map.getLayer(PARCEL_FILL_LAYER_ID)) {
    map.addLayer({
      id: PARCEL_FILL_LAYER_ID,
      type: 'fill',
      source: PARCEL_SOURCE_ID,
      paint: {
        'fill-color': '#5eead4',
        'fill-opacity': 0.18,
      },
    })
  }

  if (!map.getLayer(PARCEL_OUTLINE_LAYER_ID)) {
    map.addLayer({
      id: PARCEL_OUTLINE_LAYER_ID,
      type: 'line',
      source: PARCEL_SOURCE_ID,
      paint: {
        'line-color': '#f8fafc',
        'line-opacity': 0.95,
        'line-width': 3,
      },
    })
  }
}

function resetFlatCamera(map) {
  map.setPitch(FLAT_CAMERA.pitch)
  map.setBearing(FLAT_CAMERA.bearing)
}

function ensureFlatStyle(map) {
  if (map.getTerrain()) map.setTerrain(null)
  map.setProjection('mercator')

  const layers = map.getStyle().layers || []
  layers
    .filter((layer) => layer.type === 'fill-extrusion' || layer.type === 'sky')
    .forEach((layer) => {
      if (map.getLayer(layer.id)) map.removeLayer(layer.id)
    })

  resetFlatCamera(map)
}

function collectCoordinatePairs(coordinates, pairs = []) {
  if (!Array.isArray(coordinates)) return pairs
  if (
    coordinates.length >= 2
    && Number.isFinite(Number(coordinates[0]))
    && Number.isFinite(Number(coordinates[1]))
  ) {
    pairs.push([Number(coordinates[0]), Number(coordinates[1])])
    return pairs
  }
  coordinates.forEach((item) => collectCoordinatePairs(item, pairs))
  return pairs
}

function geometryBounds(geometry) {
  const pairs = collectCoordinatePairs(geometry?.coordinates)
  if (pairs.length === 0) return null
  const bounds = pairs.reduce(
    (currentBounds, coordinate) => currentBounds.extend(coordinate),
    new mapboxgl.LngLatBounds(pairs[0], pairs[0]),
  )
  return bounds.isEmpty() ? null : bounds
}

export function ParcelMap({ context, geometry, geometryError, tone, loading, notice, onIdentify }) {
  const containerRef = useRef(null)
  const mapRef = useRef(null)
  const markerRef = useRef(null)
  const centroid = useMemo(() => getCentroid(context?.parcel), [context])
  const parcelGeometry = geometry?.geometry ? geometry : null
  const hasOutline = Boolean(parcelGeometry?.geometry)

  useEffect(() => {
    if (!MAPBOX_TOKEN || !containerRef.current || mapRef.current) return

    mapboxgl.accessToken = MAPBOX_TOKEN
    mapRef.current = new mapboxgl.Map({
      container: containerRef.current,
      style: 'mapbox://styles/mapbox/dark-v11',
      center: SOUTH_FLORIDA_CENTER,
      zoom: 8.4,
      ...FLAT_CAMERA,
      projection: 'mercator',
      antialias: true,
    })
    mapRef.current.dragRotate.disable()
    mapRef.current.touchZoomRotate.disableRotation()
    mapRef.current.keyboard.disableRotation()
    mapRef.current.addControl(
      new mapboxgl.NavigationControl({ showCompass: false, showZoom: true, visualizePitch: false }),
      'bottom-right',
    )
    mapRef.current.on('load', () => {
      ensureFlatStyle(mapRef.current)
      ensureParcelLayers(mapRef.current)
    })

    return () => {
      markerRef.current?.remove()
      mapRef.current?.remove()
      markerRef.current = null
      mapRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!mapRef.current || !centroid || hasOutline) return

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
      ...FLAT_CAMERA,
      speed: 0.85,
      curve: 1.2,
      essential: true,
    })
  }, [centroid, context, hasOutline, tone])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    const updateGeometry = () => {
      ensureParcelLayers(map)
      const source = map.getSource(PARCEL_SOURCE_ID)
      source?.setData(parcelGeometry || { type: 'FeatureCollection', features: [] })

      if (!parcelGeometry?.geometry) return

      markerRef.current?.remove()
      markerRef.current = null
      const bounds = geometryBounds(parcelGeometry.geometry)
      if (bounds) {
        map.fitBounds(bounds, {
          padding: { top: 120, right: 90, bottom: 110, left: 90 },
          maxZoom: 17.2,
          ...FLAT_CAMERA,
          duration: 950,
          essential: true,
        })
      }
    }

    if (map.isStyleLoaded()) {
      updateGeometry()
    } else {
      map.once('load', updateGeometry)
    }
  }, [parcelGeometry])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !onIdentify) return

    const canvas = map.getCanvas()
    const previousCursor = canvas.style.cursor
    canvas.style.cursor = 'crosshair'

    const handleClick = (event) => {
      onIdentify(event.lngLat)
    }

    map.on('click', handleClick)
    return () => {
      map.off('click', handleClick)
      canvas.style.cursor = previousCursor
    }
  }, [onIdentify])

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
        <span>
          {loading === 'identify'
            ? 'Identifying parcel...'
            : loading === 'context'
              ? 'Loading parcel context...'
            : hasOutline
              ? 'Selected parcel outline'
              : notice
                ? notice
                : geometryError
                  ? 'Geometry unavailable'
                  : 'Click map or search to select'}
        </span>
        <span>{hasOutline ? 'Fitted to boundary' : 'Boundary loads per selected parcel'}</span>
      </div>
    </section>
  )
}
