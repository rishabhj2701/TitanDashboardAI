import { useEffect, useState } from 'react'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import './index.css'
import App from './App.tsx'
import { AuthProvider } from './auth/AuthContext'
import RequireAuth from './auth/RequireAuth'
import LoginPage from './pages/LoginPage'
import { getCapabilities, getCapabilityReason, isCapabilityAvailable, type CapabilitiesResponse } from './api/capabilitiesClient'
import { ENABLE_CHART_EDITING } from './config.ts'

function Root() {
  const [capabilities, setCapabilities] = useState<CapabilitiesResponse | null>(null)
  const [capabilityError, setCapabilityError] = useState<string | null>(null)

  useEffect(() => {
    let mounted = true
    const loadCapabilities = async () => {
      try {
        const data = await getCapabilities()
        if (!mounted) return
        setCapabilities(data)
        setCapabilityError(null)
      } catch (error) {
        if (!mounted) return
        setCapabilityError(error instanceof Error ? error.message : 'Failed to load capabilities')
      }
    }
    void loadCapabilities()
    return () => {
      mounted = false
    }
  }, [])

  const chartEditingEnabled =
    ENABLE_CHART_EDITING && isCapabilityAvailable(capabilities, 'chart_editing')
  const areaAnalysisTimeoutMs =
    Number(capabilities?.limits?.area_analysis_timeout_ms) > 0
      ? Number(capabilities?.limits?.area_analysis_timeout_ms)
      : 300_000

  const chartEditingDisabledReason =
    !ENABLE_CHART_EDITING
      ? 'Chart editing disabled by frontend flag (VITE_ENABLE_CHART_EDITING).'
      : getCapabilityReason(capabilities, 'chart_editing') ||
        capabilityError ||
        'Backend did not advertise chart-editing support.'

  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/"
          element={
            <RequireAuth>
              <App
                chartEditingEnabled={chartEditingEnabled}
                chartEditingDisabledReason={chartEditingDisabledReason}
                areaAnalysisTimeoutMs={areaAnalysisTimeoutMs}
              />
            </RequireAuth>
          }
        />
      </Routes>
    </AuthProvider>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <Root />
    </BrowserRouter>
  </StrictMode>,
)
