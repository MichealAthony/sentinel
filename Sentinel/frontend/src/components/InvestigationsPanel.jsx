import { useEffect, useRef, useCallback, useState } from 'react'
import { investigations as invApi } from '../api/pipeline'

const POLL_INTERVAL_MS = 15000

function Sparkline({ values }) {
  if (!values || values.length < 2) return null
  const w = 52, h = 18
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * w
    const y = h - ((v - min) / range) * (h - 2) - 1
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  return (
    <svg width={w} height={h} style={{ flexShrink: 0, display: 'block', overflow: 'visible' }}>
      <polyline points={pts} fill="none" stroke="#4895ef" strokeWidth="1.5"
        strokeLinejoin="round" strokeLinecap="round" opacity="0.8" />
    </svg>
  )
}

function ConfPill({ confidence }) {
  if (confidence == null) return null
  const pct = Math.round(confidence * 100)
  const color = pct >= 80 ? '#2dc653' : pct >= 60 ? '#f4a261' : '#e63946'
  return (
    <span style={{
      fontSize: 10, fontWeight: 600, color,
      background: `${color}1a`, borderRadius: 3, padding: '1px 5px',
      border: `0.5px solid ${color}55`, flexShrink: 0, lineHeight: 1,
    }}>
      {pct}%
    </span>
  )
}

export default function InvestigationsPanel({
  investigations,
  activeId,
  onSelect,
  onClose,
  onUpdate,
  onMarkRecovery,
}) {
  const pollTimers  = useRef({})
  const historyRef  = useRef({})   // { inv_id: number[] } rolling sighting counts

  const [now, setNow]               = useState(Date.now())
  const [editingId, setEditingId]   = useState(null)
  const [editLabel, setEditLabel]   = useState('')
  const [noteValues, setNoteValues] = useState({})
  const [expandedNotes, setExpandedNotes] = useState(new Set())
  const [sortBy, setSortBy]         = useState('newest')

  // Live "time since" counter — re-renders every 30 s
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 30000)
    return () => clearInterval(t)
  }, [])

  // Track sighting count history for sparkline
  useEffect(() => {
    investigations.forEach(inv => {
      const count = (inv.result?.sightings || []).filter(s => !s.inferred).length
      const prev  = historyRef.current[inv.id] || []
      if (prev[prev.length - 1] !== count) {
        historyRef.current[inv.id] = [...prev.slice(-19), count]
      }
    })
  }, [investigations])

  // Seed noteValues from investigation data on first load
  useEffect(() => {
    setNoteValues(prev => {
      const next = { ...prev }
      investigations.forEach(inv => {
        if (next[inv.id] === undefined) next[inv.id] = inv.notes || ''
      })
      return next
    })
  }, [investigations])

  function normaliseGet(raw) {
    const { investigation_id, label, status, notes, pinned, created_at, updated_at, ...resultFields } = raw
    return {
      label, status,
      notes:  notes  || '',
      pinned: !!pinned,
      result: Object.keys(resultFields).length ? resultFields : null,
    }
  }

  const startPolling = useCallback((inv) => {
    if (pollTimers.current[inv.id]) return
    async function poll() {
      try {
        const data = await invApi.poll(inv.id, inv.last_checked_at || inv.created_at)
        if (data.has_update) {
          const raw = await invApi.get(inv.id)
          onUpdate(inv.id, { ...normaliseGet(raw), has_update: true, last_checked_at: Date.now() / 1000 })
        }
      } catch (e) {}
      pollTimers.current[inv.id] = setTimeout(poll, POLL_INTERVAL_MS)
    }
    pollTimers.current[inv.id] = setTimeout(poll, POLL_INTERVAL_MS)
  }, [onUpdate])

  const stopPolling = useCallback((id) => {
    if (pollTimers.current[id]) {
      clearTimeout(pollTimers.current[id])
      delete pollTimers.current[id]
    }
  }, [])

  useEffect(() => {
    investigations.forEach(inv => startPolling(inv))
    return () => { investigations.forEach(inv => stopPolling(inv.id)) }
  }, [investigations, startPolling, stopPolling])

  async function handleRefresh(inv) {
    try {
      const raw = await invApi.get(inv.id)
      onUpdate(inv.id, { ...normaliseGet(raw), has_update: false, last_checked_at: Date.now() / 1000 })
    } catch (e) {}
  }

  function handleClose(e, id) {
    e.stopPropagation()
    const inv = investigations.find(i => i.id === id)
    if (!window.confirm(`Close investigation "${inv?.label || id}"?`)) return
    stopPolling(id)
    onClose(id)
  }

  function saveLabel(id) {
    const label = editLabel.trim()
    if (label) {
      onUpdate(id, { label })
      invApi.patch(id, { label }).catch(() => {})
    }
    setEditingId(null)
  }

  function saveNotes(id) {
    const notes = noteValues[id] || ''
    onUpdate(id, { notes })
    invApi.patch(id, { notes }).catch(() => {})
  }

  function togglePin(e, inv) {
    e.stopPropagation()
    const pinned = !inv.pinned
    onUpdate(inv.id, { pinned })
    invApi.patch(inv.id, { pinned }).catch(() => {})
  }

  function toggleNotes(e, id) {
    e.stopPropagation()
    setExpandedNotes(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  function timeSince(ts) {
    if (!ts) return ''
    const diff = Math.floor((now - ts * 1000) / 1000)
    if (diff < 60)   return `${diff}s ago`
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
    return `${Math.floor(diff / 3600)}h ago`
  }

  const sorted = [...investigations].sort((a, b) => {
    const pinDiff = (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0)
    if (pinDiff !== 0) return pinDiff
    if (sortBy === 'sightings') {
      const ca = (a.result?.sightings || []).filter(s => !s.inferred).length
      const cb = (b.result?.sightings || []).filter(s => !s.inferred).length
      return cb - ca
    }
    if (sortBy === 'last_seen') {
      return (b.result?.last_known_timestamp || 0) - (a.result?.last_known_timestamp || 0)
    }
    return b.created_at - a.created_at
  })

  if (investigations.length === 0) {
    return (
      <div style={{
        width: 260, flexShrink: 0,
        background: 'var(--bg-surface)',
        borderLeft: '0.5px solid var(--border)',
        display: 'flex', flexDirection: 'column',
      }}>
        <div style={{ padding: '16px' }}>
          <div style={{
            fontSize: 11, fontWeight: 700, letterSpacing: '1px',
            color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: 14,
          }}>
            Active investigations
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-dim)', lineHeight: 1.6 }}>
            No active investigations. Start a search to create one.
          </div>
        </div>
      </div>
    )
  }

  return (
    <div style={{
      width: 260, flexShrink: 0,
      background: 'var(--bg-surface)',
      borderLeft: '0.5px solid var(--border)',
      display: 'flex', flexDirection: 'column',
      overflowY: 'auto',
    }}>
      {/* Panel header */}
      <div style={{ padding: '12px 16px 8px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <div style={{
          fontSize: 11, fontWeight: 700, letterSpacing: '1px',
          color: 'var(--text-muted)', textTransform: 'uppercase', flexShrink: 0,
        }}>
          Investigations ({investigations.length})
        </div>
        {/* Sort pills */}
        <div style={{ display: 'flex', gap: 3 }}>
          {[['newest', 'New'], ['sightings', 'Hits'], ['last_seen', 'Recent']].map(([val, lbl]) => (
            <button
              key={val}
              onClick={() => setSortBy(val)}
              style={{
                fontSize: 9, padding: '2px 5px', cursor: 'pointer',
                background: sortBy === val ? 'var(--bg-raised)' : 'transparent',
                color: sortBy === val ? 'var(--text-muted)' : 'var(--text-dim)',
                border: `0.5px solid ${sortBy === val ? 'var(--border-accent)' : 'transparent'}`,
                borderRadius: 3,
              }}
            >
              {lbl}
            </button>
          ))}
        </div>
      </div>

      <div style={{ padding: '0 10px 16px', display: 'flex', flexDirection: 'column', gap: 6 }}>
        {sorted.map(inv => {
          const isActive       = inv.id === activeId
          const isPending      = inv.status === 'pending' && !inv.result
          const result         = inv.result || {}
          const sightingCount  = (result.sightings || []).filter(s => !s.inferred).length
          const lastSeen       = result.last_known_timestamp
          const sparklineData  = historyRef.current[inv.id]
          const notesExpanded  = expandedNotes.has(inv.id)
          const noteText       = noteValues[inv.id] ?? inv.notes ?? ''

          return (
            <div
              key={inv.id}
              onClick={() => { onSelect(inv.id); onUpdate(inv.id, { ...inv, has_update: false }) }}
              style={{
                background: isActive ? 'var(--bg-raised)' : 'var(--bg)',
                border: `0.5px solid ${isPending ? 'var(--blue-border)' : isActive ? 'var(--red-border)' : 'var(--border)'}`,
                boxShadow: inv.pinned ? 'inset 3px 0 0 #f4a261' : undefined,
                borderRadius: 'var(--radius-lg)',
                padding: '10px 12px',
                cursor: 'pointer',
                transition: 'border-color 0.15s',
                position: 'relative',
              }}
            >
              {/* Update dots */}
              {isPending && (
                <div className="watch-dot" style={{ position: 'absolute', top: -3, right: -3, border: '1.5px solid var(--bg-surface)' }} />
              )}
              {!isPending && inv.has_update && (
                <div style={{
                  position: 'absolute', top: -4, right: -4,
                  width: 10, height: 10, borderRadius: '50%',
                  background: 'var(--red)', boxShadow: '0 0 6px var(--red)',
                  border: '1.5px solid var(--bg-surface)',
                }} />
              )}

              {/* ── Label row ── */}
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 4, marginBottom: 5 }}>
                {/* Pin toggle */}
                <button
                  onClick={(e) => togglePin(e, inv)}
                  title={inv.pinned ? 'Unpin' : 'Pin to top'}
                  style={{
                    padding: 0, border: 'none', background: 'transparent',
                    cursor: 'pointer', flexShrink: 0, marginTop: 2,
                    lineHeight: 1,
                  }}
                >
                  <span style={{
                    display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
                    background: inv.pinned ? '#f4a261' : 'transparent',
                    border: `1.5px solid ${inv.pinned ? '#f4a261' : 'var(--text-dim)'}`,
                    opacity: inv.pinned ? 1 : 0.45,
                    transition: 'all 0.15s',
                  }} />
                </button>

                <div style={{ flex: 1, minWidth: 0 }}>
                  {/* Inline rename */}
                  {editingId === inv.id ? (
                    <input
                      value={editLabel}
                      onChange={e => setEditLabel(e.target.value)}
                      onBlur={() => saveLabel(inv.id)}
                      onKeyDown={e => {
                        if (e.key === 'Enter') saveLabel(inv.id)
                        if (e.key === 'Escape') setEditingId(null)
                      }}
                      onClick={e => e.stopPropagation()}
                      autoFocus
                      style={{
                        width: '100%', fontSize: 12, fontWeight: 500,
                        background: 'var(--bg-surface)', color: 'var(--text)',
                        border: '0.5px solid var(--border-accent)',
                        borderRadius: 3, padding: '1px 4px', outline: 'none',
                        boxSizing: 'border-box',
                      }}
                    />
                  ) : (
                    <div
                      onClick={e => { e.stopPropagation(); setEditingId(inv.id); setEditLabel(inv.label || inv.id.slice(0, 8)) }}
                      title="Click to rename"
                      style={{
                        fontSize: 12, fontWeight: 500,
                        color: isActive ? 'var(--text)' : 'var(--text-muted)',
                        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                        cursor: 'text',
                      }}
                    >
                      {inv.label || inv.id.slice(0, 8)}
                    </div>
                  )}

                  {/* Plate + confidence pill */}
                  {(result.plate || inv.params?.plate) && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginTop: 2 }}>
                      {result.colour_rgb && (
                        <div style={{
                          width: 9, height: 9, borderRadius: '50%', flexShrink: 0,
                          background: `rgb(${result.colour_rgb.join(',')})`,
                          border: '0.5px solid var(--border-accent)',
                        }} />
                      )}
                      <span style={{
                        fontFamily: 'var(--font-mono)', fontSize: 11,
                        color: isPending ? 'var(--blue)' : 'var(--red)',
                        letterSpacing: '1px',
                      }}>
                        {result.plate || inv.params?.plate}
                      </span>
                      <ConfPill confidence={result.confidence?.overall ?? result.confidence} />
                    </div>
                  )}
                </div>

                <button
                  onClick={(e) => handleClose(e, inv.id)}
                  style={{
                    padding: '2px 5px', fontSize: 11, flexShrink: 0,
                    color: 'var(--text-dim)', border: 'none', background: 'transparent', cursor: 'pointer',
                  }}
                  title="Close investigation"
                >✕</button>
              </div>

              {/* ── Body ── */}
              {isPending ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, color: 'var(--blue)' }}>
                  <div className="watch-dot" style={{ position: 'static', width: 6, height: 6 }} />
                  Watching for first sighting…
                </div>
              ) : (
                <>
                  {/* Sightings + time + sparkline */}
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 3 }}>
                    <div style={{ fontSize: 11, color: 'var(--text-dim)' }}>
                      <span>{sightingCount} sighting{sightingCount !== 1 ? 's' : ''}</span>
                      {lastSeen && <span style={{ marginLeft: 5 }}>{timeSince(lastSeen)}</span>}
                      {!lastSeen && <span style={{ marginLeft: 5 }}>no sightings</span>}
                    </div>
                    <Sparkline values={sparklineData} />
                  </div>

                  {result.last_known_camera && (
                    <div style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 2 }}>
                      Last: {result.last_known_camera}
                    </div>
                  )}

                  {result.predicted_next?.length > 0 && (
                    <div style={{
                      marginTop: 4, padding: '4px 8px',
                      background: 'var(--amber-dim)',
                      border: '0.5px solid rgba(244,162,97,0.2)',
                      borderRadius: 'var(--radius)', fontSize: 11, color: 'var(--amber)',
                    }}>
                      → {result.predicted_next[0].label || result.predicted_next[0].camera_id}
                      {' '}({Math.round((result.predicted_next[0].probability || 0) * 100)}%)
                    </div>
                  )}

                  {result.staleness_warning && (
                    <div style={{ fontSize: 10, color: 'var(--amber)', marginTop: 4 }}>
                      ⚠ Some indexes were stale
                    </div>
                  )}

                  {/* Notes */}
                  <div style={{ marginTop: 7 }} onClick={e => e.stopPropagation()}>
                    {(noteText || notesExpanded) ? (
                      <textarea
                        value={noteText}
                        onChange={e => setNoteValues(prev => ({ ...prev, [inv.id]: e.target.value }))}
                        onBlur={() => saveNotes(inv.id)}
                        placeholder="Add notes…"
                        rows={2}
                        style={{
                          width: '100%', resize: 'vertical', fontSize: 11,
                          background: 'var(--bg)', color: 'var(--text-dim)',
                          border: '0.5px solid var(--border)', borderRadius: 3,
                          padding: '4px 6px', outline: 'none', boxSizing: 'border-box',
                          fontFamily: 'inherit', lineHeight: 1.5,
                        }}
                      />
                    ) : (
                      <button
                        onClick={(e) => toggleNotes(e, inv.id)}
                        style={{
                          fontSize: 10, color: 'var(--text-dim)', background: 'transparent',
                          border: 'none', cursor: 'pointer', padding: 0, opacity: 0.55,
                        }}
                      >
                        + Add note
                      </button>
                    )}
                  </div>

                  <div style={{ display: 'flex', gap: 5, marginTop: 8 }} onClick={e => e.stopPropagation()}>
                    {isActive && (
                      <button
                        onClick={(e) => { e.stopPropagation(); handleRefresh(inv) }}
                        className="btn-ghost"
                        style={{ flex: 1, padding: '5px', fontSize: 11 }}
                      >
                        Refresh
                      </button>
                    )}
                    <button
                      onClick={(e) => { e.stopPropagation(); onMarkRecovery(inv) }}
                      style={{
                        flex: 1, padding: '5px', fontSize: 11, cursor: 'pointer',
                        background: 'rgba(45,198,83,0.08)',
                        color: '#2dc653',
                        border: '0.5px solid rgba(45,198,83,0.3)',
                        borderRadius: 'var(--radius)',
                      }}
                      title="Mark vehicle as recovered"
                    >
                      ✓ Recovered
                    </button>
                  </div>
                </>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
