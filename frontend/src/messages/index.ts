// Aggregates every per-section message namespace into the two locale bundles
// next-intl expects. Each section owns its own {en,ar}/<name>.json pair so
// independent translation passes never touch the same file.

import enCommon from './en/common.json'
import enNav from './en/nav.json'
import enCommandCenter from './en/commandCenter.json'
import enCopilot from './en/copilot.json'
import enLogin from './en/login.json'
import enLanding from './en/landing.json'
import enProjects from './en/projects.json'
import enDataWorkspace from './en/dataWorkspace.json'
import enRelationshipErd from './en/relationshipErd.json'
import enVisualization from './en/visualization.json'
import enInsights from './en/insights.json'
import enRootCause from './en/rootCause.json'
import enMonitoring from './en/monitoring.json'
import enForecasting from './en/forecasting.json'
import enMarketing from './en/marketing.json'
import enChurn from './en/churn.json'
import enCrm from './en/crm.json'
import enReports from './en/reports.json'
import enAccount from './en/account.json'
import enSettings from './en/settings.json'

import arCommon from './ar/common.json'
import arNav from './ar/nav.json'
import arCommandCenter from './ar/commandCenter.json'
import arCopilot from './ar/copilot.json'
import arLogin from './ar/login.json'
import arLanding from './ar/landing.json'
import arProjects from './ar/projects.json'
import arDataWorkspace from './ar/dataWorkspace.json'
import arRelationshipErd from './ar/relationshipErd.json'
import arVisualization from './ar/visualization.json'
import arInsights from './ar/insights.json'
import arRootCause from './ar/rootCause.json'
import arMonitoring from './ar/monitoring.json'
import arForecasting from './ar/forecasting.json'
import arMarketing from './ar/marketing.json'
import arChurn from './ar/churn.json'
import arCrm from './ar/crm.json'
import arReports from './ar/reports.json'
import arAccount from './ar/account.json'
import arSettings from './ar/settings.json'

export const messages = {
  en: {
    common: enCommon,
    nav: enNav,
    commandCenter: enCommandCenter,
    copilot: enCopilot,
    login: enLogin,
    landing: enLanding,
    projects: enProjects,
    dataWorkspace: enDataWorkspace,
    relationshipErd: enRelationshipErd,
    visualization: enVisualization,
    insights: enInsights,
    rootCause: enRootCause,
    monitoring: enMonitoring,
    forecasting: enForecasting,
    marketing: enMarketing,
    churn: enChurn,
    crm: enCrm,
    reports: enReports,
    account: enAccount,
    settings: enSettings,
  },
  ar: {
    common: arCommon,
    nav: arNav,
    commandCenter: arCommandCenter,
    copilot: arCopilot,
    login: arLogin,
    landing: arLanding,
    projects: arProjects,
    dataWorkspace: arDataWorkspace,
    relationshipErd: arRelationshipErd,
    visualization: arVisualization,
    insights: arInsights,
    rootCause: arRootCause,
    monitoring: arMonitoring,
    forecasting: arForecasting,
    marketing: arMarketing,
    churn: arChurn,
    crm: arCrm,
    reports: arReports,
    account: arAccount,
    settings: arSettings,
  },
} as const

export type Locale = keyof typeof messages
