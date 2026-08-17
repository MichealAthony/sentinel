import React, { useState, useEffect } from 'react'
import { cameras as camerasApi } from '../api/pipeline'

export default function CamerasPanel({ onMapPickerActivate, pickedLocation }) {
  const [cameraList, setCameraList] = useState([])
  const [form, setForm] = useState({
    camera_id: '', stream_url: '', base_priority: '5',
    source_fps: '30', label: '', lat: '', lng: ''
  })
  const [msg, setMsg] = useState(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    camerasApi.list().then(setCameraList).catch(() => {})
  }, [])

  useEffect(() => {
    if (pickedLocation) {
      setForm(f => ({ ...f, lat: pickedLocation.lat.toFixed(5), lng: pickedLocation.lng.toFixed(5) }))
    }
  }, [pickedLocation])

  function set(key) {
    return e => setForm(f => ({ ...f, [key]: e.target.value }))
  }

  async function handleAdd() {
    if (!form.camera_id || !form.stream_url) {
      setMsg({ type: 'err', text: 'Camera ID and stream URL are required' })
      return
    }
    setLoading(true)
    setMsg(null)
    try {
      await camerasApi.add({
        camera_id: form.camera_id,
        stream_url: form.stream_url,
        base_priority: parseInt(form.base_priority) || 5,
        source_fps: parseFloat(form.source_fps) || 30,
        location: {
          label: form.label,
          lat: parseFloat(form.lat) || 0,
          lng: parseFloat(form.lng) || 0,
        }
      })
      setMsg({ type: 'ok', text: `${form.camera_id} registered` })
      setForm({ camera_id: '', stream_url: '', base_priority: '5', source_fps: '30', label: '', lat: '', lng: '' })
      const updated = await camerasApi.list()
      setCameraList(updated)
    } catch (e) {
      setMsg({ type: 'err', text: 'Failed to register camera' })
    }
    setLoading(false)
  }

  async function handleRemove(id) {
    try {
      await camerasApi.remove(id)
      setCameraList(prev => prev.filter(c => c.camera_id !== id))
    } catch (e) {
      setMsg({ type: 'err', text: `Failed to remove ${id}` })
    }
  }

  return (
    <div style={{ padding: '16px' }}>
      <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: '1px', color: 'var(--text-dim)', textTransform: 'uppercase', marginBottom: 14 }}>
        Cameras ({cameraList.length})
      </div>

      {cameraList.length === 0 ? (
        <div style={{ fontSize: 12, color: 'var(--text-dim)', marginBottom: 16 }}>No cameras registered.</div>
      ) : (
        <div style={{ marginBottom: 16 }}>
          {cameraList.map(cam => (
            <div key={cam.camera_id} style={{
              background: 'var(--bg-raised)', border: '0.5px solid var(--border)',
              borderRadius: 'var(--radius)', padding: '10px 12px', marginBottom: 6,
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <div>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 500 }}>{cam.camera_id}</div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
                    {cam.location?.label || 'No label'} · Priority {cam.base_priority} · {cam.source_fps ?? 30}fps
                  </div>
                  <div style={{ fontSize: 10, color: 'var(--text-dim)', marginTop: 2, fontFamily: 'var(--font-mono)' }}>
                    {cam.stream_url?.length > 36 ? cam.stream_url.slice(-36) + '…' : cam.stream_url}
                  </div>
                </div>
                <button
                  className="btn-ghost"
                  onClick={() => handleRemove(cam.camera_id)}
                  style={{ fontSize: 11, padding: '4px 8px', flexShrink: 0 }}
                >Remove</button>
              </div>
              {cam.location?.lat && (
                <div style={{ fontSize: 10, color: 'var(--text-dim)', marginTop: 4, fontFamily: 'var(--font-mono)' }}>
                  {cam.location.lat.toFixed(4)}, {cam.location.lng.toFixed(4)}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: '1px', color: 'var(--text-dim)', textTransform: 'uppercase', marginBottom: 12 }}>
        Add camera
      </div>

      {msg && (
        <div style={{
          padding: '8px 10px', borderRadius: 'var(--radius)', marginBottom: 10, fontSize: 12,
          background: msg.type === 'ok' ? 'var(--green-dim)' : 'var(--red-dim)',
          color: msg.type === 'ok' ? 'var(--green)' : 'var(--red)',
          border: `0.5px solid ${msg.type === 'ok' ? 'rgba(45,198,83,0.3)' : 'var(--red-border)'}`,
        }}>{msg.text}</div>
      )}

      <FormField label="Camera ID">
        <input type="text" value={form.camera_id} onChange={set('camera_id')} placeholder="CAM_04" style={{ fontFamily: 'var(--font-mono)' }} />
      </FormField>
      <FormField label="Stream URL">
        <input type="text" value={form.stream_url} onChange={set('stream_url')} placeholder="rtsp://... or /path/to/video.mp4" style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }} />
      </FormField>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 10 }}>
        <FormField label="Base priority (1–10)">
          <input type="text" value={form.base_priority} onChange={set('base_priority')} />
        </FormField>
        <FormField label="Source FPS">
          <input type="text" value={form.source_fps} onChange={set('source_fps')} />
        </FormField>
      </div>
      <FormField label="Location label">
        <input type="text" value={form.label} onChange={set('label')} placeholder="North exit chokepoint" />
      </FormField>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 6 }}>
        <FormField label="Latitude">
          <input type="text" value={form.lat} onChange={set('lat')} placeholder="18.0179" />
        </FormField>
        <FormField label="Longitude">
          <input type="text" value={form.lng} onChange={set('lng')} placeholder="-76.8099" />
        </FormField>
      </div>
      <button
        className="btn-ghost"
        onClick={onMapPickerActivate}
        style={{ width: '100%', marginBottom: 8, fontSize: 12 }}
      >⊕ Pick coordinates on map</button>
      <button
        className="btn-primary"
        onClick={handleAdd}
        disabled={loading}
        style={{ width: '100%', padding: '9px', opacity: loading ? 0.6 : 1 }}
      >{loading ? 'Registering…' : 'Register camera'}</button>
    </div>
  )
}

function FormField({ label, children }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>{label}</div>
      {children}
    </div>
  )
}
