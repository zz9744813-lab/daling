import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { Project } from '../types'

interface ProjectState {
  currentProject: Project | null
  setCurrentProject: (project: Project | null) => void
  clearCurrentProject: () => void
}

/**
 * 当前项目 store —— 持久化到 localStorage
 */
export const useProjectStore = create<ProjectState>()(
  persist(
    (set) => ({
      currentProject: null,
      setCurrentProject: (project) => set({ currentProject: project }),
      clearCurrentProject: () => set({ currentProject: null }),
    }),
    {
      name: 'naos-current-project',
    },
  ),
)
