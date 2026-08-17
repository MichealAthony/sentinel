import React, { useState, useCallback, useEffect } from 'react'
import TopBar from './components/TopBar.jsx'
import SearchPanel from './components/SearchPanel.jsx'
import ResultsPanel from './components/ResultsPanel.jsx'
import PipelinePanel from './components/PipelinePanel.jsx'
import CamerasPanel from './components/CamerasPanel.jsx'
import StreamsPanel from './components/StreamsPanel.jsx'
import MapView from './components/MapView.jsx'
import InvestigationsPanel from './components/InvestigationsPanel.jsx'
import CameraPopup from './components/CameraPopup.jsx'
import { usePipelineStatus } from './hooks/usePipelineStatus.js'
import { investigations as invApi, cameras as camerasApi, recoveries as recovApi } from './api/pipeline.js'

export default function App() {
  const [tab, setTab]                     = useState('search')
  const [mapPickerActive, setMapPickerActive] = useState(false)
  const [pickedLocation, setPickedLocation]   = useState(null)
  const [cameraList, setCameraList]           = useState([])
  const [selectedCamera, setSelectedCamera]   = useState(null)
  const [creating, setCreating]               = useState(false)

  // Investigations state
  const [investigationList, setInvestigationList] = useState([])  // [{ id, label, result, has_update, created_at, last_checked_at }]
  const [activeInvestigationId, setActiveInvestigationId] = useState(null)

  // Recovery state
  const [recoveryList, setRecoveryList]             = useState([])
  const [recoveryPickerActive, setRecoveryPickerActive] = useState(false)
  const [recoveryPickerInv, setRecoveryPickerInv]   = useState(null)

  const { status, error, refresh } = usePipelineStatus(5000)
  const schedulerStatus = status?.cameras || []

  // Load cameras for map pins
  useEffect(() => {
    camerasApi.list().then(setCameraList).catch(() => {})
  }, [tab])

  // Load persisted recovery log on mount
  useEffect(() => {
    recovApi.list().then(setRecoveryList).catch(() => {})
  }, [])

  // Restore investigations from backend on mount (survives page refresh)
  useEffect(() => {
    invApi.list().then(async (summaries) => {
      if (summaries.length === 0) return
      const restored = await Promise.all(
        summaries.map(async (s) => {
          try {
            const raw = await invApi.get(s.investigation_id)
            const { investigation_id, label, status, notes, pinned, created_at, updated_at, ...resultFields } = raw
            return {
              id: s.investigation_id,
              label: s.label,
              status: s.status || 'active',
              params: {},
              notes: notes || '',
              pinned: !!pinned,
              result: Object.keys(resultFields).length ? resultFields : null,
              has_update: false,
              created_at: s.created_at,
              last_checked_at: Date.now() / 1000,
            }
          } catch {
            return null
          }
        })
      )
      const valid = restored.filter(Boolean)
      if (valid.length > 0) {
        setInvestigationList(valid)
        setActiveInvestigationId(valid[0].id)
      }
    }).catch(() => {})
  }, [])

  // =========================================================
  // INVESTIGATION MANAGEMENT
  // =========================================================

  const handleCreateInvestigation = useCallback(async (params) => {
    setCreating(true)
    const id = `inv_${Date.now()}`
    const label = params.label || params.plate || params.vehicle_type || params.colour || 'Investigation'

    // Optimistically add the investigation immediately
    const newInv = {
      id,
      label,
      params,
      result: null,
      status: 'loading',
      has_update: false,
      created_at: Date.now() / 1000,
      last_checked_at: Date.now() / 1000,
    }

    setInvestigationList(prev => [...prev, newInv])
    setActiveInvestigationId(id)

    // Call search API
    try {
      const apiResult = await invApi.create(params)
      const realId = apiResult.investigation_id || id

      if (apiResult.status === 'pending') {
        // Vehicle not in DB yet — investigation is watching for first sighting
        setInvestigationList(prev => prev.map(inv =>
          inv.id === id
            ? { ...inv, id: realId, result: null, status: 'pending', has_update: false }
            : inv
        ))
      } else {
        // Active result — vehicle found and trajectory built
        setInvestigationList(prev => prev.map(inv =>
          inv.id === id
            ? { ...inv, id: realId, result: apiResult, status: 'active', has_update: false }
            : inv
        ))
      }
      setActiveInvestigationId(realId)
    } catch (e) {
      // Search endpoint not yet live — store params, show placeholder
      setInvestigationList(prev => prev.map(inv =>
        inv.id === id
          ? {
              ...inv,
              status: 'error',
              result: {
                _placeholder: true,
                plate: params.plate,
                vehicle_type: params.vehicle_type,
                colour_label: params.colour,
                sightings: [],
                predicted_next: [],
                _message: 'Search endpoint not yet available. Connect the search team\'s API.',
              }
            }
          : inv
      ))
    }

    setCreating(false)
  }, [])

  const handleUpdateInvestigation = useCallback((id, updates) => {
    setInvestigationList(prev => prev.map(inv =>
      inv.id === id ? { ...inv, ...updates } : inv
    ))
  }, [])

  const handleCloseInvestigation = useCallback((id) => {
    setInvestigationList(prev => {
      const next = prev.filter(inv => inv.id !== id)
      setActiveInvestigationId(curr => curr === id ? (next[0]?.id || null) : curr)
      return next
    })
    invApi.close(id).catch(() => {})
  }, [])

  // =========================================================
  // RECOVERY MANAGEMENT
  // =========================================================

  const handleMarkRecovery = useCallback((inv) => {
    setRecoveryPickerInv(inv)
    setRecoveryPickerActive(true)
  }, [])

  const handleRecoveryPicked = useCallback(async ({ lat, lng }) => {
    setRecoveryPickerActive(false)
    const inv = recoveryPickerInv
    setRecoveryPickerInv(null)
    if (!inv) return
    try {
      const r = await recovApi.create({
        investigation_id: inv.id,
        label: inv.label,
        lat,
        lng,
        notes: '',
        plate:        inv.result?.plate || inv.params?.plate || null,
        vehicle_type: inv.result?.vehicle_type || inv.params?.vehicle_type || null,
        colour_label: inv.result?.colour_label || inv.params?.colour || null,
        reported_at:  inv.created_at || null,
      })
      setRecoveryList(prev => [r, ...prev])
    } catch (e) {}
  }, [recoveryPickerInv])

  const handleCancelRecoveryPicker = useCallback(() => {
    setRecoveryPickerActive(false)
    setRecoveryPickerInv(null)
  }, [])

  // =========================================================
  // MAP INTERACTIONS
  // =========================================================

  const handleLocationPicked = useCallback((latlng) => {
    setPickedLocation(latlng)
    setMapPickerActive(false)
  }, [])

  const handleCameraClick = useCallback((camera) => {
    setSelectedCamera(camera)
  }, [])

  // Active investigation's result for map rendering
  const activeInvestigation = investigationList.find(i => i.id === activeInvestigationId)
  const activeResult = activeInvestigation?.result?._placeholder ? null : activeInvestigation?.result

  const showMap = tab === 'search' || tab === 'cameras' || tab === 'pipeline'
  const showInvestigations = investigationList.length > 0 || tab === 'search'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
      <TopBar
        activeTab={tab}
        onTabChange={(t) => { setTab(t); setSelectedCamera(null) }}
        pipelineRunning={status?.running}
        error={error}
        investigationCount={investigationList.length}
        unreadCount={investigationList.filter(i => i.has_update).length}
      />

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>

        {/* LEFT SIDEBAR */}
        <div style={{
          width: 288, flexShrink: 0,
          background: 'var(--bg-surface)',
          borderRight: '0.5px solid var(--border)',
          overflowY: 'auto',
          display: 'flex', flexDirection: 'column',
        }}>
          {tab === 'search' && (
            <>
              <SearchPanel
                onCreateInvestigation={handleCreateInvestigation}
                onActivateMapPicker={() => setMapPickerActive(true)}
                pickedLocation={pickedLocation}
                creating={creating}
              />

              {/* Pending — watching for first sighting */}
              {activeInvestigation?.status === 'pending' && !activeInvestigation?.result && (
                <div style={{ padding: '0 16px 16px', borderTop: '0.5px solid var(--border)' }}>
                  <div style={{
                    background: 'var(--blue-dim)',
                    border: '0.5px solid var(--blue-border)',
                    borderRadius: 'var(--radius-lg)',
                    padding: '14px',
                    marginTop: 14,
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                      <div className="watch-dot" />
                      <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--blue)' }}>
                        Watching for vehicle
                      </span>
                    </div>
                    {activeInvestigation.params?.plate && (
                      <div style={{
                        fontFamily: 'var(--font-mono)', fontSize: 15, fontWeight: 500,
                        letterSpacing: '2px', color: 'var(--text)', marginBottom: 6,
                      }}>
                        {activeInvestigation.params.plate}
                      </div>
                    )}
                    <div style={{ fontSize: 11, color: 'var(--text-dim)', lineHeight: 1.6 }}>
                      No sightings yet. Investigation will auto-populate when the vehicle
                      appears on the network.
                    </div>
                  </div>
                </div>
              )}

              {/* Active result */}
              {activeInvestigation?.result && (
                activeInvestigation.result._placeholder
                  ? (
                    <div style={{ padding: '0 16px 16px', borderTop: '0.5px solid var(--border)' }}>
                      <div style={{ fontSize: 12, color: 'var(--amber)', marginTop: 12 }}>
                        {activeInvestigation.result._message}
                      </div>
                    </div>
                  )
                  : <ResultsPanel result={activeInvestigation.result} />
              )}
            </>
          )}
          {tab === 'pipeline' && (
            <PipelinePanel status={status} onRefresh={refresh} />
          )}
          {tab === 'cameras' && (
            <CamerasPanel
              onMapPickerActivate={() => setMapPickerActive(true)}
              pickedLocation={pickedLocation}
            />
          )}
          {tab === 'streams' && (
            <StreamsPanel />
          )}
        </div>

        {/* MAP / MAIN AREA */}
        <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
          {showMap ? (
            <MapView
              result={activeResult}
              cameras={cameraList}
              schedulerStatus={schedulerStatus}
              mapPickerActive={mapPickerActive}
              onLocationPicked={handleLocationPicked}
              pickedLocation={pickedLocation}
              onCameraClick={handleCameraClick}
              recoveries={recoveryList}
              recoveryPickerActive={recoveryPickerActive}
              onRecoveryPicked={handleRecoveryPicked}
              onCancelRecoveryPicker={handleCancelRecoveryPicker}
            />
          ) : (
            <div style={{
              width: '100%', height: '100%',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              color: 'var(--text-dim)', fontSize: 13,
            }}>
              Select a camera stream from the panel
            </div>
          )}

          {/* Camera popup — shown over the map */}
          {selectedCamera && (
            <CameraPopup
              camera={selectedCamera}
              schedulerStatus={schedulerStatus}
              onClose={() => setSelectedCamera(null)}
              onBoosted={() => { refresh(); setTimeout(refresh, 2000) }}
            />
          )}
        </div>

        {/* RIGHT PANEL — active investigations */}
        {showInvestigations && (
          <InvestigationsPanel
            investigations={investigationList}
            activeId={activeInvestigationId}
            onSelect={setActiveInvestigationId}
            onClose={handleCloseInvestigation}
            onUpdate={handleUpdateInvestigation}
            onMarkRecovery={handleMarkRecovery}
          />
        )}

      </div>
    </div>
  )
}
