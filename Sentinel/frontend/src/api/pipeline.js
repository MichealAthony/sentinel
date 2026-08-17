const BASE = '/api'

async function request(path, options = {}) {
  const res = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options
  })
  if (!res.ok) throw new Error(`API error ${res.status}`)
  return res.json()
}

export const pipeline = {
  start:       () => request('/pipeline/start', { method: 'POST' }),
  stop:        () => request('/pipeline/stop',  { method: 'POST' }),
  status:      () => request('/pipeline/status'),
  publisher:   () => request('/pipeline/publisher'),
  diagnostics: () => request('/pipeline/diagnostics'),
  boost: (camera_ids, boost_amount, ttl_seconds) =>
    request('/pipeline/boost', {
      method: 'POST',
      body: JSON.stringify({ camera_ids, boost_amount, ttl_seconds })
    }),
}

export const cameras = {
  list:   () => request('/cameras'),
  add:    (data) => request('/cameras', { method: 'POST', body: JSON.stringify(data) }),
  remove: (id)  => request(`/cameras/${id}`, { method: 'DELETE' }),
}

export const investigations = {
  // Create a new investigation from search params.
  // Returns { investigation_id, ...initial_result }
  create: (params) => request('/search/investigations', {
    method: 'POST',
    body: JSON.stringify(params)
  }),

  // List all active investigations.
  // Returns [{ investigation_id, label, created_at, last_updated, has_update, result }]
  list: () => request('/search/investigations'),

  // Get the current full result for one investigation.
  get: (id) => request(`/search/investigations/${id}`),

  // Lightweight poll — checks if new sightings exist since last_checked_at (unix float).
  // Returns { has_update: bool, new_sighting_count: int, last_known_timestamp: float }
  poll: (id, last_checked_at) =>
    request(`/search/investigations/${id}/poll?since=${last_checked_at}`),

  // Update label, notes, or pinned flag.
  patch: (id, data) => request(`/search/investigations/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  }),

  // Close and remove an investigation.
  close: (id) => request(`/search/investigations/${id}`, { method: 'DELETE' }),
}

export const recoveries = {
  list:   () => request('/search/recoveries'),
  create: (data) => request('/search/recoveries', { method: 'POST', body: JSON.stringify(data) }),
  delete: (id)  => request(`/search/recoveries/${id}`, { method: 'DELETE' }),
}
