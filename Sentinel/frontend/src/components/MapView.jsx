import { useEffect, useRef } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'

const DEFAULT_CENTER = [18.0179, -76.8099]
const DEFAULT_ZOOM   = 14

delete L.Icon.Default.prototype._getIconUrl
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
  iconUrl:       'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
  shadowUrl:     'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
})

function makeNumberedIcon(number, isInferred = false) {
  return L.divIcon({
    className: '',
    html: `<div style="
      width:28px;height:28px;border-radius:50%;
      background:${isInferred ? '#2a2a38' : '#e63946'};
      border:2px solid ${isInferred ? '#3a3a50' : 'rgba(230,57,70,0.5)'};
      display:flex;align-items:center;justify-content:center;
      font-family:'DM Mono',monospace;font-size:11px;font-weight:500;
      color:${isInferred ? '#5a5a70' : '#fff'};
      box-shadow:${isInferred ? 'none' : '0 0 12px rgba(230,57,70,0.4)'};
    ">${number}</div>`,
    iconSize: [28, 28], iconAnchor: [14, 14],
  })
}

function makePredictionLabel(probability) {
  return L.divIcon({
    className: '',
    html: `<div style="
      font-family:'DM Mono',monospace;font-size:10px;font-weight:600;
      color:#f4a261;background:rgba(14,14,20,0.75);
      padding:2px 5px;border-radius:3px;
      border:0.5px solid rgba(244,162,97,0.35);
      white-space:nowrap;pointer-events:none;
    ">${Math.round(probability * 100)}%</div>`,
    iconSize: [32, 16], iconAnchor: [16, 24],
  })
}

function makeCameraIcon(isActive, isBoosted) {
  const borderColor = isBoosted ? 'rgba(244,162,97,0.8)' : isActive ? 'rgba(45,198,83,0.7)' : 'rgba(72,149,239,0.5)'
  const dotColor    = isBoosted ? '#f4a261' : isActive ? '#2dc653' : '#4895ef'
  return L.divIcon({
    className: '',
    html: `<div style="
      width:22px;height:22px;border-radius:5px;
      background:#111118;border:1.5px solid ${borderColor};
      display:flex;align-items:center;justify-content:center;
      cursor:pointer;
    ">
      <div style="width:8px;height:8px;border-radius:2px;background:${dotColor};"></div>
    </div>`,
    iconSize: [22, 22], iconAnchor: [11, 11],
  })
}

function makeRecoveryIcon() {
  return L.divIcon({
    className: '',
    html: `<div style="
      width:28px;height:28px;border-radius:50%;
      background:#2dc653;border:2px solid rgba(45,198,83,0.4);
      display:flex;align-items:center;justify-content:center;
      font-size:14px;color:#fff;font-weight:700;
      box-shadow:0 0 12px rgba(45,198,83,0.5);
    ">✓</div>`,
    iconSize: [28, 28], iconAnchor: [14, 14],
  })
}

function makePickedLocationIcon() {
  return L.divIcon({
    className: '',
    html: `<div style="
      width:14px;height:14px;border-radius:50%;
      background:#e63946;border:2px solid rgba(230,57,70,0.4);
      box-shadow:0 0 10px rgba(230,57,70,0.5);
    "></div>`,
    iconSize: [14, 14], iconAnchor: [7, 7],
  })
}

function getBearing(a, b) {
  const lat1 = a.lat * Math.PI / 180, lat2 = b.lat * Math.PI / 180
  const dLng = (b.lng - a.lng) * Math.PI / 180
  const y = Math.sin(dLng) * Math.cos(lat2)
  const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLng)
  return ((Math.atan2(y, x) * 180 / Math.PI) + 360) % 360
}

export default function MapView({
  result,
  cameras,
  schedulerStatus,
  mapPickerActive,
  onLocationPicked,
  pickedLocation,
  onCameraClick,
  recoveries,
  recoveryPickerActive,
  onRecoveryPicked,
  onCancelRecoveryPicker,
}) {
  const mapRef      = useRef(null)
  const mapInstance = useRef(null)
  const layers      = useRef({ route: [], markers: [], cameras: [], prediction: [], picked: null, recoveries: [] })
  const animRef     = useRef(null)

  // Init map
  useEffect(() => {
    if (mapInstance.current) return
    mapInstance.current = L.map(mapRef.current, {
      center: DEFAULT_CENTER, zoom: DEFAULT_ZOOM, zoomControl: false,
    })
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '© OpenStreetMap contributors', maxZoom: 19,
    }).addTo(mapInstance.current)
    L.control.zoom({ position: 'bottomright' }).addTo(mapInstance.current)
  }, [])

  // Map picker click — handles both location picker and recovery picker
  useEffect(() => {
    if (!mapInstance.current) return
    const map = mapInstance.current
    function handleClick(e) {
      if (recoveryPickerActive) {
        onRecoveryPicked({ lat: e.latlng.lat, lng: e.latlng.lng })
        return
      }
      if (mapPickerActive) {
        onLocationPicked({ lat: e.latlng.lat, lng: e.latlng.lng })
      }
    }
    map.on('click', handleClick)
    map.getContainer().style.cursor = (mapPickerActive || recoveryPickerActive) ? 'crosshair' : ''
    return () => { map.off('click', handleClick); map.getContainer().style.cursor = '' }
  }, [mapPickerActive, recoveryPickerActive, onLocationPicked, onRecoveryPicked])

  // Picked location pin
  useEffect(() => {
    if (!mapInstance.current) return
    if (layers.current.picked) { layers.current.picked.remove(); layers.current.picked = null }
    if (pickedLocation) {
      layers.current.picked = L.marker([pickedLocation.lat, pickedLocation.lng], {
        icon: makePickedLocationIcon()
      }).addTo(mapInstance.current).bindPopup('Last known location')
    }
  }, [pickedLocation])

  // Recovery markers — rebuilt whenever the recovery log changes
  useEffect(() => {
    if (!mapInstance.current) return
    layers.current.recoveries.forEach(l => l.remove())
    layers.current.recoveries = (recoveries || []).map(r => {
      const date = new Date(r.created_at * 1000).toLocaleString([], {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
      })
      const reportedDate = r.reported_at
        ? new Date(r.reported_at * 1000).toLocaleString([], { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' })
        : '—'

      const rows = [
        r.plate        && ['Plate',          `<span style="font-family:monospace;font-weight:600;">${r.plate}</span>`],
        r.vehicle_type && ['Vehicle type',   r.vehicle_type],
        r.colour_label && ['Colour',         r.colour_label],
        ['Reported',      reportedDate],
        ['Date recovered', date],
      ].filter(Boolean)

      const rowsHtml = rows.map(([label, val]) => `
        <div style="display:flex;justify-content:space-between;gap:12px;font-size:11px;margin-bottom:3px;">
          <span style="color:#888;white-space:nowrap;">${label}</span>
          <span style="color:#333;text-align:right;">${val}</span>
        </div>
      `).join('')

      return L.marker([r.lat, r.lng], { icon: makeRecoveryIcon(), zIndexOffset: 200 })
        .addTo(mapInstance.current)
        .bindPopup(`
          <div style="font-family:var(--font-display);min-width:180px;padding:2px 0;">
            <div style="font-weight:700;font-size:13px;color:#2dc653;margin-bottom:8px;">✓ Vehicle Recovered</div>
            ${rowsHtml}
            ${r.notes ? `<div style="font-size:11px;color:#666;margin-top:8px;padding-top:6px;border-top:1px solid #eee;">${r.notes}</div>` : ''}
          </div>
        `)
    })
  }, [recoveries])

  // Camera pins — rebuilt when cameras or scheduler status changes
  useEffect(() => {
    if (!mapInstance.current || !cameras?.length) return
    layers.current.cameras.forEach(l => l.remove())
    layers.current.cameras = cameras.map(cam => {
      const { lat, lng, label } = cam.location || {}
      if (!lat || !lng) return null
      const schedEntry = schedulerStatus?.find(s => s.camera_id === cam.camera_id)
      const isActive   = schedEntry?.active || false
      const isBoosted  = (schedEntry?.boost || 0) > 0

      const marker = L.marker([lat, lng], {
        icon: makeCameraIcon(isActive, isBoosted),
        // Higher zIndex so camera pins are always clickable
        zIndexOffset: 100,
      }).addTo(mapInstance.current)

      // Click opens the camera info popup via parent callback
      marker.on('click', (e) => {
        L.DomEvent.stopPropagation(e)
        if (!mapPickerActive) onCameraClick(cam)
      })

      // Minimal tooltip on hover
      marker.bindTooltip(
        `<span style="font-family:monospace;font-size:11px;">${cam.camera_id}</span><br/>${label || ''}`,
        { direction: 'top', offset: [0, -14], className: '' }
      )

      return marker
    }).filter(Boolean)
  }, [cameras, schedulerStatus, mapPickerActive, onCameraClick])

  // Route, sighting markers, and vehicle simulation
  useEffect(() => {
    if (!mapInstance.current) return
    const map = mapInstance.current

    if (animRef.current) { cancelAnimationFrame(animRef.current); animRef.current = null }
    layers.current.route.forEach(l => l.remove())
    layers.current.markers.forEach(l => l.remove())
    layers.current.prediction.forEach(l => l.remove())
    layers.current.route = []
    layers.current.markers = []
    layers.current.prediction = []

    if (result) {
      const { sightings = [], predicted_next = [] } = result
      const valid = sightings.filter(s => !s.inferred && s.lat && s.lng)

      // Route: faint solid trail underneath + animated flowing dashes on top
      for (let i = 0; i < valid.length - 1; i++) {
        const a = valid[i], b = valid[i + 1]

        layers.current.route.push(L.polyline([[a.lat, a.lng], [b.lat, b.lng]], {
          color: '#e63946', weight: 3, opacity: 0.2,
        }).addTo(map))

        layers.current.route.push(L.polyline([[a.lat, a.lng], [b.lat, b.lng]], {
          color: '#e63946', weight: 3, opacity: 0.9, className: 'route-flow-line',
        }).addTo(map))

        const mid = L.latLng((a.lat + b.lat) / 2, (a.lng + b.lng) / 2)
        const bearing = getBearing(a, b)
        layers.current.route.push(L.marker(mid, {
          icon: L.divIcon({
            className: '',
            html: `<div style="width:18px;height:18px;display:flex;align-items:center;justify-content:center;transform:rotate(${bearing}deg);">
              <svg width="10" height="16" viewBox="0 0 10 16" xmlns="http://www.w3.org/2000/svg">
                <polygon points="5,0 10,16 5,10 0,16" fill="#e63946" stroke="rgba(255,255,255,0.28)" stroke-width="0.6" stroke-linejoin="round"/>
              </svg>
            </div>`,
            iconSize: [18, 18], iconAnchor: [9, 9],
          })
        }).addTo(map))
      }

      // Inferred gap lines (dashed)
      for (let i = 0; i < valid.length - 1; i++) {
        const idxA = sightings.indexOf(valid[i])
        const idxB = sightings.indexOf(valid[i + 1])
        if (sightings.slice(idxA + 1, idxB).some(s => s.inferred)) {
          const a = valid[i], b = valid[i + 1]
          layers.current.route.push(L.polyline([[a.lat, a.lng], [b.lat, b.lng]], {
            color: '#3a3a50', weight: 2, opacity: 0.7, dashArray: '6 5',
          }).addTo(map))
        }
      }

      // Numbered sighting markers
      let markerNum = 1
      sightings.forEach(s => {
        if (!s.lat || !s.lng) return
        const marker = L.marker([s.lat, s.lng], { icon: makeNumberedIcon(markerNum, s.inferred) }).addTo(map)

        // Permanent label: location, date, time, confidence
        if (!s.inferred) {
          const dateStr = s.timestamp
            ? new Date(s.timestamp * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' })
            : ''
          const timeStr = s.timestamp
            ? new Date(s.timestamp * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
            : ''
          const confPct = s.confidence != null ? Math.round(s.confidence * 100) : null
          const dateTime = [dateStr, timeStr].filter(Boolean).join(', ')
          const metaLine = [dateTime, confPct != null ? `${confPct}%` : ''].filter(Boolean).join(' · ')
          marker.bindTooltip(
            `<div style="font-weight:600;font-size:11px;line-height:1.3;">${s.label || s.camera_id || 'Unknown'}</div>${metaLine ? `<div style="font-size:10px;color:rgba(255,255,255,0.5);margin-top:1px;">${metaLine}</div>` : ''}`,
            { permanent: true, direction: 'top', offset: [0, -16], className: 'sentinel-sighting-label' }
          )
        }

        // Rich click popup
        const timeStr = s.timestamp
          ? new Date(s.timestamp * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
          : null
        const confPct = s.confidence != null ? Math.round(s.confidence * 100) : null
        const confBand = confPct != null
          ? confPct >= 85 ? 'high' : confPct >= 65 ? 'medium' : confPct >= 40 ? 'low' : 'very low'
          : null
        const confColor = confPct != null
          ? confPct >= 85 ? '#2dc653' : confPct >= 65 ? '#f4a261' : '#e63946'
          : '#888'

        marker.bindPopup(`
          <div style="font-family:var(--font-display);min-width:160px;padding:2px 0;">
            <div style="font-weight:600;font-size:13px;margin-bottom:3px;">${s.label || s.camera_id || 'Unknown'}</div>
            ${s.camera_id ? `<div style="font-size:11px;color:#888;font-family:'DM Mono',monospace;margin-bottom:6px;">${s.camera_id}</div>` : ''}
            ${timeStr ? `<div style="font-size:12px;color:#555;margin-bottom:4px;">${timeStr}</div>` : ''}
            ${confPct != null ? `<div style="font-size:12px;display:flex;align-items:center;gap:6px;">
              <span style="color:${confColor};font-weight:600;">${confPct}%</span>
              <span style="color:#999;">${confBand} confidence</span>
            </div>` : ''}
            ${s.inferred ? `<div style="font-size:11px;color:#f4a261;margin-top:6px;padding-top:6px;border-top:1px solid #eee;">⚠ Gap inferred${s.gap_seconds ? ` · ~${Math.round(s.gap_seconds / 60)} min` : ''}</div>` : ''}
          </div>
        `)

        layers.current.markers.push(marker)
        markerNum++
      })

      // Prediction heatmap — multi-colour rings: yellow outer → orange → bright red center
      // Colors ordered outer→inner (i=0 is outermost, i=steps-1 is innermost / hottest)
      const HEAT_COLORS = [
        '#ffe566', '#ffd200', '#ffaa00', '#ff8000',
        '#ff5500', '#ff2a00', '#ff0000', '#e60000',
        '#cc0022', '#b0003a',
      ]
      predicted_next.forEach((p, idx) => {
        if (!p.lat || !p.lng) return
        const prob = p.probability || 0
        const maxRadius = 480 * (0.4 + prob * 0.6)  // scale footprint with probability
        const steps = HEAT_COLORS.length

        for (let i = 0; i < steps; i++) {
          const t = i / (steps - 1)                          // 0=outer, 1=inner
          const radius = maxRadius * (1 - t * 0.72)          // outer ring is widest
          const fillOpacity = (Math.pow(t, 0.75) * 0.82 + 0.04) * prob
          layers.current.prediction.push(
            L.circle([p.lat, p.lng], {
              radius,
              stroke: false,
              fillColor: HEAT_COLORS[i],
              fillOpacity,
              interactive: i === steps - 1,
            }).addTo(map)
            .bindPopup(`Predicted: ${p.label || p.camera_id} (${Math.round(prob * 100)}%)`)
          )
        }

        // Probability label on top 3 predictions only
        if (idx < 3) {
          layers.current.prediction.push(
            L.marker([p.lat, p.lng], { icon: makePredictionLabel(prob), interactive: false }).addTo(map)
          )
        }
      })

      // Grey dashed lines from last confirmed sighting to each predicted point
      // Rendered after heatmap circles so they sit on top in z-order
      const lastConfirmed = valid[valid.length - 1]
      if (lastConfirmed) {
        predicted_next.forEach((p) => {
          if (!p.lat || !p.lng) return
          layers.current.prediction.push(
            L.polyline([[lastConfirmed.lat, lastConfirmed.lng], [p.lat, p.lng]], {
              color: '#ff6b00', weight: 2.5, opacity: 0.9,
              dashArray: '5 7', interactive: false,
            }).addTo(map)
          )
        })
      }

      // Glowing vehicle dot that replays the route on a loop
      if (valid.length >= 2) {
        const vehicleDot = L.marker([valid[0].lat, valid[0].lng], {
          icon: L.divIcon({
            className: '',
            html: `<div style="width:12px;height:12px;border-radius:50%;background:#e63946;border:2px solid rgba(255,255,255,0.7);box-shadow:0 0 14px rgba(230,57,70,1),0 0 6px rgba(230,57,70,0.6);"></div>`,
            iconSize: [12, 12], iconAnchor: [6, 6],
          }),
          zIndexOffset: 300,
          interactive: false,
        }).addTo(map)
        layers.current.route.push(vehicleDot)

        const DURATION = 9000
        const numSegments = valid.length - 1
        let startTime = null

        function animate(ts) {
          if (!startTime) startTime = ts
          const progress = ((ts - startTime) % DURATION) / DURATION
          const raw = progress * numSegments
          const seg = Math.min(Math.floor(raw), numSegments - 1)
          const t = raw - seg
          const a = valid[seg], b = valid[seg + 1] || valid[seg]
          vehicleDot.setLatLng([a.lat + (b.lat - a.lat) * t, a.lng + (b.lng - a.lng) * t])
          animRef.current = requestAnimationFrame(animate)
        }
        animRef.current = requestAnimationFrame(animate)
      }

      // Fit bounds
      const allPoints = [
        ...valid.map(s => [s.lat, s.lng]),
        ...predicted_next.filter(p => p.lat && p.lng).map(p => [p.lat, p.lng]),
      ]
      if (allPoints.length > 0) {
        map.fitBounds(L.latLngBounds(allPoints), { padding: [60, 60], maxZoom: 16 })
      }
    }

    return () => { if (animRef.current) { cancelAnimationFrame(animRef.current); animRef.current = null } }
  }, [result])

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <div ref={mapRef} style={{ width: '100%', height: '100%' }} />
      {(mapPickerActive || recoveryPickerActive) && (
        <div style={{
          position: 'absolute', bottom: 16, left: '50%', transform: 'translateX(-50%)',
          background: 'var(--bg-surface)', border: `0.5px solid ${recoveryPickerActive ? 'rgba(45,198,83,0.5)' : 'var(--border-accent)'}`,
          borderRadius: 'var(--radius)', padding: '8px 16px',
          fontSize: 12, color: recoveryPickerActive ? '#2dc653' : 'var(--text-muted)',
          zIndex: 1000, display: 'flex', alignItems: 'center', gap: 10,
        }}>
          <span style={{ pointerEvents: 'none' }}>
            {recoveryPickerActive ? '✓ Click map to mark recovery location' : '⊕ Click map to set last known location'}
          </span>
          {recoveryPickerActive && (
            <button
              onClick={onCancelRecoveryPicker}
              style={{
                fontSize: 11, color: 'var(--text-dim)', background: 'transparent',
                border: 'none', cursor: 'pointer', padding: 0, lineHeight: 1,
              }}
            >
              Cancel
            </button>
          )}
        </div>
      )}
    </div>
  )
}
