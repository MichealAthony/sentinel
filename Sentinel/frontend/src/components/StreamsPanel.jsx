import React, { useState, useEffect } from 'react'
import { cameras as camerasApi } from '../api/pipeline'

const PIPELINE_BASE = ''

export default function StreamsPanel() {
  const [cameraList, setCameraList] = useState([])
  const [selected, setSelected] = useState(null)

  useEffect(() => {
    camerasApi.list()
      .then(data => {
        setCameraList(data)
        if (data.length > 0) setSelected(data[0].camera_id)
      })
      .catch(() => {})
  }, [])

  return (
    <div style={{ padding: '16px' }}>
      <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: '1px', color: 'var(--text-dim)', textTransform: 'uppercase', marginBottom: 14 }}>
        Live streams
      </div>

      {cameraList.length === 0 ? (
        <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>
          No cameras registered. Add cameras first.
        </div>
      ) : (
        <>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 14 }}>
            {cameraList.map(cam => (
              <button
                key={cam.camera_id}
                onClick={() => setSelected(cam.camera_id)}
                style={{
                  padding: '5px 10px', fontSize: 11,
                  fontFamily: 'var(--font-mono)',
                  background: selected === cam.camera_id ? 'var(--blue-dim)' : 'transparent',
                  borderColor: selected === cam.camera_id ? 'var(--blue-border)' : 'var(--border)',
                  color: selected === cam.camera_id ? 'var(--blue)' : 'var(--text-muted)',
                }}
              >{cam.camera_id}</button>
            ))}
          </div>

          {selected && (
            <div>
              <div style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 8 }}>
                {cameraList.find(c => c.camera_id === selected)?.location?.label || selected}
              </div>
              <div style={{
                background: 'var(--bg-raised)',
                borderRadius: 'var(--radius-lg)',
                border: '0.5px solid var(--border)',
                overflow: 'hidden',
                aspectRatio: '16/9',
                width: '100%',
              }}>
                <img
                  src={`${PIPELINE_BASE}/stream/${selected}`}
                  alt={`Live feed ${selected}`}
                  style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
                  onError={e => { e.target.style.display = 'none' }}
                />
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 8 }}>
                Embed in any page with:
              </div>
              <div style={{
                fontFamily: 'var(--font-mono)', fontSize: 10,
                color: 'var(--text-muted)',
                background: 'var(--bg-raised)',
                borderRadius: 'var(--radius)',
                padding: '8px 10px', marginTop: 4,
                wordBreak: 'break-all',
                border: '0.5px solid var(--border)',
              }}>
                {`<img src="${PIPELINE_BASE}/stream/${selected}" />`}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
