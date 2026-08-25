import { lazy } from 'react'
import { createBrowserRouter, Navigate } from 'react-router-dom'
import { Layout } from './components/Layout'
import { Auth } from './pages/Auth'

// 代码分割: 页面全部 lazy 加载, 避免首屏打包所有页面 (ECharts / lightweight-charts /
// framer-motion 等重库) → 大幅减小首屏 bundle。命名导出用 .then 映射为 default。
// Layout / Auth 为应用外壳与入口, 保持同步加载。
const Watchlist = lazy(() => import('./pages/Watchlist').then(m => ({ default: m.Watchlist })))
const Screener = lazy(() => import('./pages/Screener').then(m => ({ default: m.Screener })))
const Backtest = lazy(() => import('./pages/Backtest').then(m => ({ default: m.Backtest })))
const FinancialUnavailable = lazy(() => import('./pages/FinancialUnavailable').then(m => ({ default: m.FinancialUnavailable })))
const Data = lazy(() => import('./pages/Data').then(m => ({ default: m.Data })))
const Monitor = lazy(() => import('./pages/Monitor').then(m => ({ default: m.Monitor })))
const AnalysisDetail = lazy(() => import('./pages/AnalysisDetail').then(m => ({ default: m.AnalysisDetail })))
const ConceptAnalysis = lazy(() => import('./pages/ConceptAnalysis').then(m => ({ default: m.ConceptAnalysis })))
const IndustryAnalysis = lazy(() => import('./pages/IndustryAnalysis').then(m => ({ default: m.IndustryAnalysis })))
const StockAnalysis = lazy(() => import('./pages/StockAnalysis').then(m => ({ default: m.StockAnalysis })))
const DailyReview = lazy(() => import('./pages/DailyReview').then(m => ({ default: m.DailyReview })))
const LimitUpLadder = lazy(() => import('./pages/LimitUpLadder').then(m => ({ default: m.LimitUpLadder })))
const Branding = lazy(() => import('./pages/Branding').then(m => ({ default: m.Branding })))
const Settings = lazy(() => import('./pages/Settings').then(m => ({ default: m.Settings })))
const Indices = lazy(() => import('./pages/Indices').then(m => ({ default: m.Indices })))
const Dev = lazy(() => import('./pages/Dev').then(m => ({ default: m.Dev })))
const GoldWorkspace = lazy(() => import('./pages/GoldWorkspace').then(m => ({ default: m.GoldWorkspace })))
const ClientConnection = lazy(() => import('./pages/ClientConnection').then(m => ({ default: m.ClientConnection })))
const PaperTrading = lazy(() => import('./pages/PaperTrading').then(m => ({ default: m.PaperTrading })))
const PaperAccount = lazy(() => import('./pages/PaperAccount').then(m => ({ default: m.PaperAccount })))

export const router = createBrowserRouter([
  { path: '/onboarding', element: <Navigate to="/settings?tab=account" replace /> },
  { path: '/login', element: <Auth /> },
  {
    path: '/',
    element: <Layout />,
    children: [
      { index: true, element: <Navigate to="/paper-trading" replace /> },
      { path: 'overview', element: <Navigate to="/paper-trading" replace /> },
      { path: 'paper-trading', element: <PaperTrading /> },
      { path: 'paper-account', element: <PaperAccount /> },
      { path: 'analysis', element: <Navigate to="/settings?tab=ext-pages" replace /> },
      { path: 'analysis/:menuId', element: <AnalysisDetail /> },
      { path: 'concept-analysis', element: <ConceptAnalysis /> },
      { path: 'industry-analysis', element: <IndustryAnalysis /> },
      { path: 'stock-analysis', element: <StockAnalysis /> },
      { path: 'review', element: <DailyReview /> },
      { path: 'watchlist', element: <Watchlist /> },
      { path: 'screener', element: <Screener /> },
      { path: 'backtest', element: <Backtest /> },
      { path: 'financials', element: <FinancialUnavailable /> },
      { path: 'data', element: <Data /> },
      { path: 'gold', element: <GoldWorkspace /> },
      { path: 'monitor', element: <Monitor /> },
      { path: 'limit-ladder', element: <LimitUpLadder /> },
      { path: 'indices', element: <Indices /> },
      { path: 'branding', element: <Branding /> },
      { path: 'settings', element: <Settings /> },
      { path: 'client-connection', element: <ClientConnection /> },
      // 隐藏路由：开发者工具（不暴露在菜单，仅供调试）
      { path: 'dev', element: <Dev /> },
      // 旧路由兼容重定向
      { path: 'settings/keys', element: <Navigate to="/settings?tab=account" replace /> },
      { path: 'settings/ai', element: <Navigate to="/settings?tab=ai" replace /> },
      { path: 'settings/queries', element: <Navigate to="/settings?tab=queries" replace /> },
    ],
  },
])
