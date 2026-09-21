import { create } from 'zustand'
import type { IngestResponse } from '@/lib/queries/ingestion'
import type { ChurnRunResult } from '@/lib/queries/churn'

export type Section =
  | 'command-center'
  | 'copilot'
  | 'projects'
  | 'data'
  | 'visualization'
  | 'insights'
  | 'root-cause'
  | 'monitoring'
  | 'forecasting'
  | 'marketing'
  | 'churn'
  | 'crm'
  | 'reports'
  | 'account'
  | 'settings'

export type User = {
  name: string
  email: string
  role: string
  organization: string
  initials: string
  plan: string
}

type AppState = {
  section: Section
  activeProjectId: string
  rightPanelOpen: boolean
  commandOpen: boolean
  sidebarCollapsed: boolean
  /** Synced from the real NextAuth session — see components/shared/session-sync.tsx. */
  user: User | null
  /** Result of the most recent ingest call (files/sheet/db-link), picked up
   * by the Data Workspace page to resume the cleaning pipeline. */
  pendingIngestion: IngestResponse | null
  /** Cross-page artifacts, mirroring Streamlit's st.session_state cross-page
   * sharing: the Marketing page can optionally ground its strategy in the
   * most recent Forecasting/Churn run from this session. */
  lastForecastOutputs: Record<string, unknown>[] | null
  lastChurnResult: ChurnRunResult | null
  /** A question typed into the global command palette, picked up by the
   * Command Center's real "Ask DAAS" panel and then cleared. */
  pendingAskQuestion: string | null
  /** Measure a "Why?" click on the Insights page wants drilled into, picked up
   * by the Root Cause section and then cleared — so the drill-down opens on the
   * question that was actually asked rather than on its own default. */
  pendingRootCauseMeasure: string | null
  setSection: (s: Section) => void
  setUser: (u: User | null) => void
  updateUser: (u: Partial<User>) => void
  setActiveProjectId: (id: string) => void
  setPendingIngestion: (result: IngestResponse | null) => void
  setLastForecastOutputs: (outputs: Record<string, unknown>[] | null) => void
  setLastChurnResult: (result: ChurnRunResult | null) => void
  setPendingAskQuestion: (question: string | null) => void
  setPendingRootCauseMeasure: (measure: string | null) => void
  toggleRightPanel: () => void
  setCommandOpen: (open: boolean) => void
  toggleSidebar: () => void
}

export const useAppStore = create<AppState>((set) => ({
  section: 'command-center',
  activeProjectId: '',
  rightPanelOpen: true,
  commandOpen: false,
  sidebarCollapsed: false,
  user: null,
  pendingIngestion: null,
  lastForecastOutputs: null,
  lastChurnResult: null,
  pendingAskQuestion: null,
  pendingRootCauseMeasure: null,
  setSection: (section) => set({ section }),
  setUser: (user) => set({ user }),
  updateUser: (u) =>
    set((s) => {
      if (!s.user) return s
      const next = { ...s.user, ...u }
      if (u.name) next.initials = u.name.split(' ').map((n) => n[0]).join('').slice(0, 2).toUpperCase()
      return { user: next }
    }),
  setActiveProjectId: (activeProjectId) => set({ activeProjectId }),
  setPendingIngestion: (pendingIngestion) => set({ pendingIngestion }),
  setLastForecastOutputs: (lastForecastOutputs) => set({ lastForecastOutputs }),
  setLastChurnResult: (lastChurnResult) => set({ lastChurnResult }),
  setPendingAskQuestion: (pendingAskQuestion) => set({ pendingAskQuestion }),
  setPendingRootCauseMeasure: (pendingRootCauseMeasure) => set({ pendingRootCauseMeasure }),
  toggleRightPanel: () => set((s) => ({ rightPanelOpen: !s.rightPanelOpen })),
  setCommandOpen: (commandOpen) => set({ commandOpen }),
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
}))
