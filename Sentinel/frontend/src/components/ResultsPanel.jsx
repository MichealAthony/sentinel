
export default function ResultsPanel({ result }) {
  if (!result) return null

  const {
    plate, vehicle_type, colour_label, colour_rgb,
    sightings = [], predicted_next = [],
    last_known_camera, last_known_timestamp, staleness_warning,
    confidence, stolen_report,
  } = result

  function formatTime(ts) {
    return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  }

  function timeSince(ts) {
    const diff = Math.floor(Date.now() / 1000 - ts)
    if (diff < 60) return `${diff}s ago`
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
    return `${Math.floor(diff / 3600)}h ago`
  }

  const confirmed = sightings.filter(s => !s.inferred)

  return (
    <div style={{ padding: '0 16px 16px', borderTop: '0.5px solid var(--border)' }}>

      {stolen_report && (
        <div style={{
          background: 'rgba(230,57,70,0.08)', border: '0.5px solid rgba(230,57,70,0.45)',
          borderRadius: 'var(--radius)', padding: '10px 12px', margin: '12px 0',
          display: 'flex', alignItems: 'flex-start', gap: 10,
        }}>
          <span style={{ fontSize: 18, lineHeight: 1 }}>🚨</span>
          <div>
            <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--red)', marginBottom: 3 }}>
              Flagged in stolen vehicle database
            </div>
            {stolen_report.reported_by && (
              <div style={{ fontSize: 11, color: 'var(--text-dim)' }}>
                Reported by: {stolen_report.reported_by}
              </div>
            )}
            {stolen_report.reported_at && (
              <div style={{ fontSize: 11, color: 'var(--text-dim)' }}>
                Reported: {new Date(stolen_report.reported_at * 1000).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' })}
              </div>
            )}
          </div>
        </div>
      )}

      {staleness_warning && (
        <div style={{
          background: 'var(--amber-dim)', border: '0.5px solid rgba(244,162,97,0.3)',
          borderRadius: 'var(--radius)', padding: '7px 10px',
          fontSize: 12, color: 'var(--amber)', margin: '12px 0',
        }}>
          ⚠ Some camera indexes were not fully current at search time.
        </div>
      )}

      <div style={{
        background: 'var(--bg-raised)', border: '0.5px solid var(--border-accent)',
        borderRadius: 'var(--radius-lg)', padding: '12px 14px', margin: '12px 0',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <div style={{
              fontFamily: 'var(--font-mono)', fontSize: 18, fontWeight: 500,
              letterSpacing: '2px', color: 'var(--text)',
            }}>
              {plate || '—'}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
              {colour_rgb && (
                <div style={{
                  width: 11, height: 11, borderRadius: '50%',
                  background: `rgb(${colour_rgb.join(',')})`,
                  border: '0.5px solid var(--border-accent)', flexShrink: 0,
                }} />
              )}
              <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                {[colour_label?.replace('_', ' '), vehicle_type].filter(Boolean).join(' ') || 'Unknown'}
              </span>
            </div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: 11, color: 'var(--text-dim)' }}>last seen</div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
              {last_known_timestamp ? timeSince(last_known_timestamp) : '—'}
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 1 }}>
              {last_known_camera || '—'}
            </div>
          </div>
        </div>
      </div>

      <div style={{ fontSize: 10, color: 'var(--text-dim)', marginBottom: 10, letterSpacing: '0.5px' }}>
        SIGHTING TIMELINE — {confirmed.length} confirmed, {sightings.length - confirmed.length} inferred
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
        {sightings.map((s, i) => (
          <div key={i} style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', flexShrink: 0 }}>
              <div style={{
                width: 22, height: 22, borderRadius: '50%',
                background: s.inferred ? 'var(--bg-raised)' : 'var(--red-dim)',
                border: `1.5px solid ${s.inferred ? 'var(--border-accent)' : 'var(--red-border)'}`,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 10, fontWeight: 600,
                color: s.inferred ? 'var(--text-dim)' : 'var(--red)',
                fontFamily: 'var(--font-mono)', flexShrink: 0,
              }}>{i + 1}</div>
              {i < sightings.length - 1 && (
                <div style={{ width: 1, height: 16, background: 'var(--border)', margin: '2px 0' }} />
              )}
            </div>
            <div style={{ paddingTop: 2, paddingBottom: 10 }}>
              <div style={{ fontSize: 13, fontWeight: s.inferred ? 400 : 500, color: s.inferred ? 'var(--text-muted)' : 'var(--text)' }}>
                {s.label || s.camera_id || 'Unknown location'}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 1 }}>
                {s.timestamp ? formatTime(s.timestamp) : '—'}
                {s.confidence != null && ` · ${Math.round(s.confidence * 100)}% conf`}
              </div>
              {s.inferred && (
                <div style={{ fontSize: 11, color: 'var(--amber)', marginTop: 2 }}>
                  ⚠ Gap inferred{s.gap_seconds ? ` (~${Math.round(s.gap_seconds / 60)}m unaccounted)` : ''}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {predicted_next?.length > 0 && (
        <div style={{ marginTop: 14, paddingTop: 14, borderTop: '0.5px solid var(--border)' }}>
          <div style={{ fontSize: 10, color: 'var(--text-dim)', marginBottom: 10, letterSpacing: '0.5px' }}>
            PREDICTED NEXT
          </div>
          {predicted_next.map((p, i) => (
            <div key={i} style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              padding: '7px 10px', marginBottom: 6,
              background: 'var(--amber-dim)',
              border: '0.5px solid rgba(244,162,97,0.25)',
              borderRadius: 'var(--radius)',
            }}>
              <span style={{ fontSize: 12, color: 'var(--text)' }}>{p.label || p.camera_id}</span>
              <span style={{ fontSize: 12, fontFamily: 'var(--font-mono)', color: 'var(--amber)' }}>
                {Math.round(p.probability * 100)}%
              </span>
            </div>
          ))}
        </div>
      )}

      {confidence?.signals && (
        <div style={{ marginTop: 14, paddingTop: 14, borderTop: '0.5px solid var(--border)' }}>
          <div style={{ fontSize: 10, color: 'var(--text-dim)', marginBottom: 10, letterSpacing: '0.5px' }}>
            CONFIDENCE — {confidence.band?.replace('_', ' ').toUpperCase()} ({Math.round((confidence.overall || 0) * 100)}%)
          </div>
          {Object.entries(confidence.signals).map(([key, sig]) => {
            const pct = Math.round((sig.score || 0) * 100)
            const barColor = pct >= 80 ? '#2dc653' : pct >= 50 ? '#f4a261' : '#e63946'
            return (
              <div key={key} style={{ marginBottom: 8 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                  <span style={{ fontSize: 11, color: 'var(--text-muted)', textTransform: 'capitalize' }}>{key}</span>
                  <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--text-dim)' }}>{pct}%</span>
                </div>
                <div style={{ height: 3, borderRadius: 2, background: 'var(--bg-raised)', overflow: 'hidden' }}>
                  <div style={{ height: '100%', width: `${pct}%`, background: barColor, borderRadius: 2 }} />
                </div>
                <div style={{ fontSize: 10, color: 'var(--text-dim)', marginTop: 2 }}>{sig.note}</div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
