import React, { useState } from 'react'
import { pipeline } from '../api/pipeline'

export default function PipelinePanel({ status, onRefresh }) {
  const [boostCams, setBoostCams] = useState('')
  const [boostAmount, setBoostAmount] = useState('20')
  const [boostTtl, setBoostTtl] = useState('300')
  const [msg, setMsg] = useState(null)
  const [loading, setLoading] = useState(null)

  async function act(fn, label) {
    setLoading(label)
    setMsg(null)
    try {
      await fn()
      setMsg({ type: 'ok', text: `${label} successful` })
      onRefresh()
    } catch (e) {
      setMsg({ type: 'err', text: `${label} failed — is the pipeline running?` })
    }
    setLoading(null)
  }

  async function handleBoost() {
    const ids = boostCams.split(',').map(s => s.trim()).filter(Boolean)
    if (!ids.length) { setMsg({ type: 'err', text: 'Enter at least one camera ID' }); return }
    await act(() => pipeline.boost(ids, parseInt(boostAmount), parseInt(boostTtl)), 'Boost')
  }

  const running = status?.running

  return (
    <div style={{ padding: '16px' }}>
      <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: '1px', color: 'var(--text-dim)', textTransform: 'uppercase', marginBottom: 14 }}>
        Pipeline control
      </div>

      {msg && (
        <div style={{
          padding: '8px 10px', borderRadius: 'var(--radius)', marginBottom: 12,
          fontSize: 12,
          background: msg.type === 'ok' ? 'var(--green-dim)' : 'var(--red-dim)',
          color: msg.type === 'ok' ? 'var(--green)' : 'var(--red)',
          border: `0.5px solid ${msg.type === 'ok' ? 'rgba(45,198,83,0.3)' : 'var(--red-border)'}`,
        }}>{msg.text}</div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 10 }}>
        <button
          className="btn-success"
          disabled={!!loading || running}
          onClick={() => act(pipeline.start, 'Start')}
          style={{ padding: '9px', opacity: running ? 0.4 : 1 }}
        >{loading === 'Start' ? '…' : 'Start'}</button>
        <button
          className="btn-primary"
          disabled={!!loading || !running}
          onClick={() => act(pipeline.stop, 'Stop')}
          style={{ padding: '9px', opacity: !running ? 0.4 : 1 }}
        >{loading === 'Stop' ? '…' : 'Stop'}</button>
      </div>

      <button className="btn-ghost" onClick={onRefresh} style={{ width: '100%', marginBottom: 16, padding: '8px' }}>
        Refresh status
      </button>

      {status && (
        <div style={{ background: 'var(--bg-raised)', borderRadius: 'var(--radius)', padding: '12px', marginBottom: 16, fontSize: 12 }}>
          <StatusRow label="State" value={running ? 'Running' : 'Stopped'} valueColor={running ? 'var(--green)' : 'var(--text-muted)'} />
          <StatusRow label="Active cameras" value={status.cameras?.length ?? '—'} />
          <StatusRow label="Active streams" value={status.active_streams?.length ?? '—'} />
          <StatusRow label="DB queue depth" value={status.publisher?.queue_depth ?? '—'} />
          <StatusRow label="DB endpoint" value={status.publisher?.running ? 'Connected' : 'Disconnected'} valueColor={status.publisher?.running ? 'var(--green)' : 'var(--red)'} />
        </div>
      )}

      {status?.cameras?.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: '1px', color: 'var(--text-dim)', textTransform: 'uppercase', marginBottom: 10 }}>
            Scheduler state
          </div>
          {status.cameras.map(cam => (
            <div key={cam.camera_id} style={{
              background: 'var(--bg-raised)', borderRadius: 'var(--radius)',
              padding: '10px 12px', marginBottom: 6,
              border: cam.active ? '0.5px solid var(--red-border)' : '0.5px solid var(--border)',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: cam.active ? 'var(--red)' : 'var(--text)' }}>
                  {cam.camera_id}
                </span>
                <span style={{ fontSize: 11, color: 'var(--text-dim)' }}>
                  eff. priority {cam.effective_priority?.toFixed(1)}
                </span>
              </div>
              <div style={{ height: 3, background: 'var(--border)', borderRadius: 2, overflow: 'hidden', marginBottom: 5 }}>
                <div style={{
                  height: '100%', borderRadius: 2,
                  width: `${Math.min(100, (cam.effective_priority / 30) * 100)}%`,
                  background: cam.boost > 0 ? 'var(--amber)' : 'var(--blue)',
                  transition: 'width 0.3s',
                }} />
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--text-dim)' }}>
                <span>base {cam.base_priority}</span>
                {cam.boost > 0 && <span style={{ color: 'var(--amber)' }}>+{cam.effective_boost?.toFixed(0)} boost · {cam.boost_remaining_seconds?.toFixed(0)}s left</span>}
                {cam.fatigue_coefficient < 1 && <span style={{ color: 'var(--text-muted)' }}>fatigue {(cam.fatigue_coefficient * 100).toFixed(0)}%</span>}
              </div>
            </div>
          ))}
        </div>
      )}

      <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: '1px', color: 'var(--text-dim)', textTransform: 'uppercase', marginBottom: 10 }}>
        Boost cameras
      </div>
      <div style={{ marginBottom: 8 }}>
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Camera IDs (comma separated)</div>
        <input type="text" value={boostCams} onChange={e => setBoostCams(e.target.value)} placeholder="CAM_01, CAM_02" />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 8 }}>
        <div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>Boost amount</div>
          <input type="text" value={boostAmount} onChange={e => setBoostAmount(e.target.value)} />
        </div>
        <div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 4 }}>TTL (seconds)</div>
          <input type="text" value={boostTtl} onChange={e => setBoostTtl(e.target.value)} />
        </div>
      </div>
      <button className="btn-primary" onClick={handleBoost} style={{ width: '100%', padding: '9px' }}>
        {loading === 'Boost' ? '…' : 'Boost cameras'}
      </button>
    </div>
  )
}

function StatusRow({ label, value, valueColor }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6, fontSize: 12 }}>
      <span style={{ color: 'var(--text-muted)' }}>{label}</span>
      <span style={{ color: valueColor || 'var(--text)', fontFamily: typeof value === 'number' ? 'var(--font-mono)' : 'inherit' }}>{value}</span>
    </div>
  )
}
