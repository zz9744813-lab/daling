import React, { Suspense, lazy } from 'react'
import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { ErrorBoundary } from './layout/ErrorBoundary'
import { useProjectStore } from './store/projectStore'

// 路由懒加载
const ProjectSelectPage = lazy(() => import('./pages/ProjectSelectPage'))
const NewProjectPage = lazy(() => import('./pages/NewProjectPage'))
const CockpitPage = lazy(() => import('./pages/CockpitPage'))
const StorylinePage = lazy(() => import('./pages/StorylinePage'))
const BrainPage = lazy(() => import('./pages/BrainPage'))
const EvolutionPage = lazy(() => import('./pages/EvolutionPage'))
const GovernancePage = lazy(() => import('./pages/GovernancePage'))

/** 页面加载占位 */
function PageFallback() {
  return (
    <div className="flex h-screen items-center justify-center bg-ink-950 text-gray-500">
      <div className="h-6 w-6 animate-spin rounded-full border-2 border-ink-600 border-t-gold-500" />
    </div>
  )
}

/**
 * 路由守卫：有 currentProject 时才允许进入受保护页面，否则重定向到 /
 */
function RequireProject({ children }: { children: React.ReactNode }) {
  const project = useProjectStore((s) => s.currentProject)
  const location = useLocation()
  if (!project) {
    return <Navigate to="/" replace state={{ from: location }} />
  }
  return <>{children}</>
}

export default function App() {
  return (
    <ErrorBoundary>
      <Suspense fallback={<PageFallback />}>
        <Routes>
          <Route path="/" element={<ProjectSelectPage />} />
          <Route path="/projects/new" element={<NewProjectPage />} />
          <Route
            path="/cockpit"
            element={
              <RequireProject>
                <CockpitPage />
              </RequireProject>
            }
          />
          <Route
            path="/storyline"
            element={
              <RequireProject>
                <StorylinePage />
              </RequireProject>
            }
          />
          <Route
            path="/brain"
            element={
              <RequireProject>
                <BrainPage />
              </RequireProject>
            }
          />
          <Route
            path="/evolution"
            element={
              <RequireProject>
                <EvolutionPage />
              </RequireProject>
            }
          />
          <Route
            path="/governance"
            element={
              <RequireProject>
                <GovernancePage />
              </RequireProject>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </ErrorBoundary>
  )
}
