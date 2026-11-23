import { Routes, Route, Navigate } from 'react-router-dom'
import { useAuthStore } from './stores/auth'
import { LoginPage } from './components/auth/LoginPage'
import { RegisterPage } from './components/auth/RegisterPage'
import { StudyDetailPage } from './components/study/StudyDetailPage'
import { RunDetailPage } from './components/run/RunDetailPage'
import { AppShell } from './components/layout/AppShell'
import { MonitorPage } from './pages/MonitorPage'
import { DAGPage } from './pages/DAGPage'
import { FactorLibraryPage } from './pages/FactorLibraryPage'
import { StrategyLibraryPage } from './pages/StrategyLibraryPage'
import { CatalogPage } from './catalog/CatalogPage'
import { CatalogItem } from './catalog/CatalogItem'
import { StudyPage } from './pages/StudyPage'
import { AccountPage } from './pages/AccountPage'

function AuthGuard({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.token)
  if (!token) return <Navigate to="/login" replace />
  return <>{children}</>
}

export default function App() {
  return (
    <>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/catalog" element={<CatalogPage />} />
        <Route path="/catalog/:name" element={<CatalogItem />} />
        <Route
          path="/study/:studyId"
          element={
            <AuthGuard>
              <StudyDetailPage />
            </AuthGuard>
          }
        />
        <Route
          path="/study"
          element={
            <AuthGuard>
              <StudyPage />
            </AuthGuard>
          }
        />
        <Route
          path="/run/:strategyName/:runName"
          element={
            <AuthGuard>
              <RunDetailPage />
            </AuthGuard>
          }
        />
        <Route
          path="/workflow"
          element={<Navigate to="/dag" replace />}
        />
        <Route
          path="/workflow-definition"
          element={<Navigate to="/dag" replace />}
        />
        <Route
          path="/"
          element={
            <AuthGuard>
              <MonitorPage />
            </AuthGuard>
          }
        />
        <Route
          path="/chat"
          element={
            <AuthGuard>
              <AppShell />
            </AuthGuard>
          }
        />
        <Route
          path="/dag"
          element={
            <AuthGuard>
              <DAGPage />
            </AuthGuard>
          }
        />
        <Route
          path="/factors"
          element={
            <AuthGuard>
              <FactorLibraryPage />
            </AuthGuard>
          }
        />
        <Route
          path="/strategies"
          element={
            <AuthGuard>
              <StrategyLibraryPage />
            </AuthGuard>
          }
        />
        <Route path="/admin/users" element={<Navigate to="/account" replace />} />
        <Route
          path="/account"
          element={
            <AuthGuard>
              <AccountPage />
            </AuthGuard>
          }
        />
        <Route
          path="/monitor"
          element={
            <AuthGuard>
              <MonitorPage />
            </AuthGuard>
          }
        />
        <Route
          path="/*"
          element={
            <AuthGuard>
              <AppShell />
            </AuthGuard>
          }
        />
      </Routes>
    </>
  )
}
