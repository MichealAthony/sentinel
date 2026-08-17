import React, { useState } from 'react'

const COLOURS = [
  'black', 'white', 'silver', 'grey', 'red', 'maroon',
  'blue', 'dark_blue', 'green', 'dark_green',
  'orange', 'yellow', 'brown', 'beige', 'gold', 'purple',
]

const COLOUR_HEX = {
  black: '#1a1a1a', white: '#f5f5f5', silver: '#c0c0c0', grey: '#808080',
  red: '#cc2222', maroon: '#7a1212', blue: '#1a5fa5', dark_blue: '#142b6b',
  green: '#2a8a2a', dark_green: '#145014', orange: '#d4721e', yellow: '#d4c020',
  brown: '#7a4020', beige: '#d4c09a', gold: '#c8a820', purple: '#7a2e7a',
}

const VEHICLE_TYPES = ['car', 'truck', 'bus', 'motorcycle']

export default function SearchPanel({
  onCreateInvestigation,
  onActivateMapPicker,
  pickedLocation,
  creating,
}) {
  const [plate, setPlate]       = useState('')
  const [type, setType]         = useState('')
  const [colour, setColour]     = useState('')
  const [lastSeen, setLastSeen] = useState('')
  const [label, setLabel]       = useState('')

  function handleSubmit() {
    if (!plate && !type && !colour) {
      alert('Enter at least one search field — plate, type, or colour.')
      return
    }
    if (!lastSeen) {
      alert('Last seen date/time is required to filter sightings correctly.')
      return
    }

    const params = {
      plate:               plate || undefined,
      vehicle_type:        type  || undefined,
      colour:              colour || undefined,
      last_seen_at:        lastSeen,
      last_known_location: pickedLocation || undefined,
      label: label || [plate, type, colour].filter(Boolean).join(' · ') || 'Search',
    }

    onCreateInvestigation(params)

    // Reset form so operator can start another search immediately
    setPlate('')
    setType('')
    setColour('')
    setLastSeen('')
    setLabel('')
  }

  return (
    <div style={{ padding: '16px' }}>
      <div style={{
        fontSize: 11, fontWeight: 700, letterSpacing: '1px',
        color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: 14,
      }}>
        New investigation
      </div>

      <Field label="Investigation label (optional)">
        <input
          type="text"
          placeholder="e.g. Red truck — Market St"
          value={label}
          onChange={e => setLabel(e.target.value)}
        />
      </Field>

      <Field label="Licence plate">
        <input
          type="text"
          placeholder="e.g. ABC1234"
          value={plate}
          onChange={e => setPlate(e.target.value.toUpperCase())}
          style={{ fontFamily: 'var(--font-mono)', letterSpacing: '1px' }}
        />
      </Field>

      <Field label="Vehicle type">
        <select value={type} onChange={e => setType(e.target.value)}>
          <option value="">Any type</option>
          {VEHICLE_TYPES.map(t => (
            <option key={t} value={t}>
              {t.charAt(0).toUpperCase() + t.slice(1)}
            </option>
          ))}
        </select>
      </Field>

      <Field label="Colour">
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <select
            value={colour}
            onChange={e => setColour(e.target.value)}
            style={{ flex: 1 }}
          >
            <option value="">Any colour</option>
            {COLOURS.map(c => (
              <option key={c} value={c}>
                {c.replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase())}
              </option>
            ))}
          </select>
          {colour && (
            <div style={{
              width: 20, height: 20, borderRadius: '50%', flexShrink: 0,
              background: COLOUR_HEX[colour] || '#888',
              border: colour === 'white' ? '0.5px solid var(--border-accent)' : 'none',
            }} />
          )}
        </div>
      </Field>

      <Field label="Last seen — filter sightings from this time">
        <input
          type="datetime-local"
          value={lastSeen}
          onChange={e => setLastSeen(e.target.value)}
        />
      </Field>

      <Field label="Last known location">
        <div style={{ display: 'flex', gap: 6 }}>
          <input
            type="text"
            readOnly
            placeholder="Click ⊕ to pick on map"
            value={pickedLocation
              ? `${pickedLocation.lat.toFixed(4)}, ${pickedLocation.lng.toFixed(4)}`
              : ''}
            style={{
              flex: 1,
              color: pickedLocation ? 'var(--text)' : 'var(--text-dim)',
            }}
          />
          <button
            onClick={onActivateMapPicker}
            title="Pick location on map"
            style={{ flexShrink: 0, padding: '8px 10px', fontSize: 14 }}
          >⊕</button>
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 4 }}>
          Click ⊕ then click the map to set location
        </div>
      </Field>

      <button
        className="btn-primary"
        onClick={handleSubmit}
        disabled={creating}
        style={{
          width: '100%', marginTop: 4, padding: '9px',
          fontWeight: 600, letterSpacing: '0.5px',
          opacity: creating ? 0.6 : 1,
          display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
        }}
      >
        {creating && <div className="watch-dot" style={{ position: 'static', width: 7, height: 7, flexShrink: 0 }} />}
        {creating ? 'Starting investigation…' : 'Start investigation'}
      </button>
    </div>
  )
}

function Field({ label, children }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 5 }}>
        {label}
      </div>
      {children}
    </div>
  )
}
