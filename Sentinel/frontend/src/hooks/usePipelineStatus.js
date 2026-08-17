import { useState, useEffect, useCallback } from 'react'
import { pipeline } from '../api/pipeline'

export function usePipelineStatus(pollInterval = 5000) {
  const [status, setStatus] = useState(null)
  const [error, setError] = useState(null)

  const refresh = useCallback(async () => {
    try {
      const data = await pipeline.status()
      setStatus(data)
      setError(null)
    } catch (e) {
      setError('Pipeline unreachable')
    }
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, pollInterval)
    return () => clearInterval(id)
  }, [refresh, pollInterval])

  return { status, error, refresh }
}
