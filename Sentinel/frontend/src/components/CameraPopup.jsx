import React, { useState } from 'react'
import { pipeline } from '../api/pipeline'

export default function CameraPopup({ camera, schedulerStatus, onClose, onBoosted }) {
  const [boostAmount, setBoostAmount] = useState('20')
  const [ttl, setTtl]                 = useState('300')
  const [boosting, setBoosting]       = useState(false)
  const [msg, setMsg]                 = useState(null)

  if (!camera) return null

  // Find this camera's scheduler entry from pipeline status
  const schedEntry = schedulerStatus?.find(c => c.camera_id === camera.camera_id)

  async function handleBoost() {
    setBoosting(true)
    setMsg(null)
    try {
      await pipeline.boost(
        [camera.camera_id],
        parseInt(boostAmount) || 20,
        parseInt(ttl) || 300,
      )
      setMsg({ type: 'ok', text: `Boosted for ${ttl}s` })
      if (onBoosted) onBoosted(camera.camera_id)
    } catch (e) {
      setMsg({ type: 'err', text: 'Boost failed' })
    }
    setBoosting(false)
  }

  const isActive   = schedEntry?.active
  const isBoosted  = schedEntry?.boost > 0
  const fatigue    = schedEntry?.fatigue_coefficient
  const effPri     = schedEntry?.effective_priority
  const waitSec    = schedEntry?.wait_seconds

  return (
    <div style={{
      position: 'absolute',
      zIndex: 2000,
      bottom: 24,
      left: '50%',
      transform: 'translateX(-50%)',
      width: 280,
      background: 'var(--bg-raised)',
      border: '0.5px solid var(--border-accent)',
      borderRadius: 'var(--radius-lg)',
      padding: '14px',
      boxShadow: '0 4px 24px rgba(0,0,0,0.4)',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
        <div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 500 }}>
            {camera.camera_id}
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
            {camera.location?.label || 'No label'}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <div style={{
            width: 7, height: 7, borderRadius: '50%',
            background: isActive ? 'var(--green)' : 'var(--text-dim)',
            boxShadow: isActive ? '0 0 5px var(--green)' : 'none',
          }} />
          <span style={{ fontSize: 11, color: isActive ? 'var(--green)' : 'var(--text-dim)' }}>
            {isActive ? 'Processing' : 'Queued'}
          </span>
          <button
            onClick={onClose}
            style={{ border: 'none', background: 'transparent', color: 'var(--text-dim)', fontSize: 14, padding: '0 4px', cursor: 'pointer' }}
          >✕</button>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px 0', marginBottom: 12 }}>
        <Row label="Base priority"     value={camera.base_priority ?? '—'} />
        <Row label="Source FPS"        value={camera.source_fps ? `${camera.source_fps} fps` : '—'} />
        <Row label="Eff. priority"     value={effPri?.toFixed(1) ?? '—'} />
        <Row label="Wait time"         value={waitSec != null ? `${waitSec.toFixed(0)}s` : '—'} />
        <Row label="Boost"             value={isBoosted ? `+${schedEntry.effective_boost?.toFixed(0)}` : 'None'} valueColor={isBoosted ? 'var(--amber)' : undefined} />
        <Row label="Fatigue"           value={fatigue != null ? `${(fatigue * 100).toFixed(0)}%` : '—'} valueColor={fatigue != null && fatigue < 0.5 ? 'var(--amber)' : undefined} />
        {camera.location?.lat && (
          <Row label="Coordinates" value={`${camera.location.lat.toFixed(3)}, ${camera.location.lng.toFixed(3)}`} span />
        )}
        {isBoosted && (
          <Row label="Boost remaining" value={`${schedEntry.boost_remaining_seconds?.toFixed(0)}s`} valueColor="var(--amber)" span />
        )}
      </div>

      <div style={{ borderTop: '0.5px solid var(--border)', paddingTop: 12 }}>
        <div style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 8 }}>
          Boost this camera
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginBottom: 8 }}>
          <div>
            <div style={{ fontSize: 10, color: 'var(--text-dim)', marginBottom: 3 }}>Amount</div>
            <input type="text" value={boostAmount} onChange={e => setBoostAmount(e.target.value)} />
          </div>
          <div>
            <div style={{ fontSize: 10, color: 'var(--text-dim)', marginBottom: 3 }}>TTL (s)</div>
            <input type="text" value={ttl} onChange={e => setTtl(e.target.value)} />
          </div>
        </div>
        {msg && (
          <div style={{
            fontSize: 11, padding: '5px 8px', borderRadius: 'var(--radius)', marginBottom: 6,
            background: msg.type === 'ok' ? 'var(--green-dim)' : 'var(--red-dim)',
            color: msg.type === 'ok' ? 'var(--green)' : 'var(--red)',
          }}>{msg.text}</div>
        )}
        <button
          className="btn-primary"
          onClick={handleBoost}
          disabled={boosting}
          style={{ width: '100%', padding: '7px', opacity: boosting ? 0.6 : 1, fontSize: 12 }}
        >{boosting ? 'Boosting…' : 'Boost camera'}</button>
      </div>
    </div>
  )
}

function Row({ label, value, valueColor, span }) {
  return (
    <>
      <div style={{ fontSize: 11, color: 'var(--text-dim)', gridColumn: span ? '1 / -1' : undefined }}>
        {label}
      </div>
      <div style={{
        fontSize: 11, color: valueColor || 'var(--text)',
        fontFamily: typeof value === 'number' ? 'var(--font-mono)' : 'inherit',
        gridColumn: span ? '1 / -1' : undefined,
      }}>
        {value}
      </div>
    </>
  )
}
