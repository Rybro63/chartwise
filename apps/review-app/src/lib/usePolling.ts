import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

/**
 * Loads data once, then reloads every intervalMs for as long as `active(data)` is true. The
 * condition is re-checked on every tick, so polling resumes by itself when a manual refresh
 * returns data that needs watching again (e.g. after approving, until the note is filed).
 * An intervalMs of 0 disables polling.
 */
export function usePolling<T>(load: () => Promise<T>, intervalMs: number, active: (data: T | undefined) => boolean) {
  const [data, setDataState] = useState<T>()
  const [error, setError] = useState<string>()
  const loadRef = useRef(load)
  const activeRef = useRef(active)
  const dataRef = useRef<T | undefined>(undefined)
  useLayoutEffect(() => {
    loadRef.current = load
    activeRef.current = active
  })

  const setData = useCallback((value: T) => {
    dataRef.current = value
    setDataState(value)
  }, [])

  const refresh = useCallback(async () => {
    try {
      const result = await loadRef.current()
      setData(result)
      setError(undefined)
      return result
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      return undefined
    }
  }, [setData])

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const schedule = () => {
      if (!cancelled && intervalMs > 0) timer = setTimeout(tick, intervalMs)
    }
    const tick = async () => {
      if (activeRef.current(dataRef.current)) await refresh()
      schedule()
    }
    void refresh().then(schedule)
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [refresh, intervalMs])

  return { data, error, refresh, setData }
}
