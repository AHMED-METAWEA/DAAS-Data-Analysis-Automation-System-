'use client'

import * as React from 'react'
import { useProjects } from '@/lib/queries/projects'
import { useAppStore } from '@/lib/store'

/** Keeps `activeProjectId` pointed at a real project. On first load (or if the
 * previously-active project was deleted), auto-selects the most recent real
 * project instead of leaving the store's default/stale id pointed at a
 * project that doesn't exist for this user — every project-scoped query
 * would otherwise 404 silently. */
export function ProjectSync() {
  const projectsQuery = useProjects()
  const activeProjectId = useAppStore((s) => s.activeProjectId)
  const setActiveProjectId = useAppStore((s) => s.setActiveProjectId)

  React.useEffect(() => {
    if (!projectsQuery.data) return
    const stillValid = projectsQuery.data.some((p) => p.id === activeProjectId)
    if (!stillValid) {
      setActiveProjectId(projectsQuery.data[0]?.id ?? '')
    }
  }, [projectsQuery.data, activeProjectId, setActiveProjectId])

  return null
}
