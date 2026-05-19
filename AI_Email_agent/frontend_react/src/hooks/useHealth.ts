import { useState, useEffect } from 'react'
import { healthApi } from '../api/client'

export function useHealth() {
  const [health, setHealth] = useState<{ status: string; version: string; db: string } | null>(null)

  useEffect(() => {
    const check = () => healthApi.check().then(setHealth).catch(() => setHealth(null))
    check()
    const id = setInterval(check, 30_000)
    return () => clearInterval(id)
  }, [])

  return health
}
