import React from 'react'

export default function TopBar({ activeTab, onTabChange, pipelineRunning, error, investigationCount, unreadCount }) {
  const tabs = [
    { id: 'search',   label: 'Search' },
    { id: 'pipeline', label: 'Pipeline' },
    { id: 'cameras',  label: 'Cameras' },
    { id: 'streams',  label: 'Streams' },
  ]

  return (
    <div style={{
      height: 52,
      background: 'var(--bg-surface)',
      borderBottom: '0.5px solid var(--border)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '0 20px',
      flexShrink: 0,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 24 }}>
        <div style={{ fontFamily: 'var(--font-display)', fontWeight: 700, fontSize: 15, letterSpacing: '-0.5px' }}>
          SENTINEL<span style={{ color: 'var(--red)', fontWeight: 400 }}>.</span>
        </div>
        <div style={{ display: 'flex', gap: 2 }}>
          {tabs.map(tab => (
            <button
              key={tab.id}
              onClick={() => onTabChange(tab.id)}
              style={{
                padding: '5px 12px', fontSize: 12,
                fontWeight: activeTab === tab.id ? 600 : 400,
                color: activeTab === tab.id ? 'var(--text)' : 'var(--text-muted)',
                background: activeTab === tab.id ? 'var(--bg-raised)' : 'transparent',
                border: '0.5px solid',
                borderColor: activeTab === tab.id ? 'var(--border-accent)' : 'transparent',
                borderRadius: 'var(--radius)',
                letterSpacing: '0.3px',
              }}
            >{tab.label}</button>
          ))}
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        {/* Investigations indicator */}
        {investigationCount > 0 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
            <span style={{ color: 'var(--text-muted)' }}>
              {investigationCount} investigation{investigationCount !== 1 ? 's' : ''}
            </span>
            {unreadCount > 0 && (
              <div style={{
                background: 'var(--red)', borderRadius: 99,
                padding: '1px 7px', fontSize: 11, fontWeight: 600,
                color: '#fff', boxShadow: '0 0 6px var(--red)',
              }}>{unreadCount} new</div>
            )}
          </div>
        )}

        {/* Pipeline status */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
          {error ? (
            <span style={{ color: 'var(--red)', fontSize: 12 }}>{error}</span>
          ) : (
            <>
              <div style={{
                width: 7, height: 7, borderRadius: '50%',
                background: pipelineRunning ? 'var(--green)' : 'var(--text-dim)',
                boxShadow: pipelineRunning ? '0 0 6px var(--green)' : 'none',
                transition: 'all 0.3s',
              }} />
              <span style={{ color: pipelineRunning ? 'var(--green)' : 'var(--text-muted)' }}>
                {pipelineRunning ? 'Pipeline running' : 'Pipeline stopped'}
              </span>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
