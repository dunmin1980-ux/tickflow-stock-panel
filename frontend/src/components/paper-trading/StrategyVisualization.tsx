import { useCallback, useRef, useState } from 'react'
import type { ResearchEngine } from '@/lib/research-engine'
import { DEFAULT_OVERLAY_FILTERS, strategyGraphicsPoints, strategyOverlayMarkers, type StrategyGraphicsData } from '@/lib/strategy-graphics'
import { StrategyGraphics, type StrategySelection } from './StrategyGraphics'
import { StrategyOverviewCards } from './StrategyOverviewCards'

export function StrategyVisualization({ graphics, engine, requestedDate }: {
  graphics: StrategyGraphicsData | undefined; engine?: ResearchEngine; requestedDate: string
}) {
  const [selection, setSelection] = useState<StrategySelection>({ selectedObservationDate: null, filters: DEFAULT_OVERLAY_FILTERS })
  const chart = useRef<HTMLDivElement>(null)
  const ready = graphics?.status === 'READY' && graphics.trade_date === engine?.trade_date ? graphics : undefined
  const points = ready ? strategyGraphicsPoints(ready) : []
  const date = points.find(point => point.trade_date === selection.selectedObservationDate)?.trade_date ?? points.at(-1)?.trade_date ?? engine?.trade_date ?? ''
  const observations = ready ? strategyOverlayMarkers(ready).filter(marker => marker.trade_date === date) : undefined
  const selectStrategy = useCallback((strategy: string) => {
    setSelection({ selectedObservationDate: date, filters: { strategy, status: 'ALL' } })
    chart.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
  }, [date])
  return <>
    {engine && <StrategyOverviewCards engine={engine} observations={observations} observationDate={date}
      selectedStrategy={selection.filters.strategy} onSelectStrategy={ready ? selectStrategy : undefined} />}
    <div ref={chart} className="min-w-0 scroll-mt-3">
      <StrategyGraphics graphics={graphics} engine={engine} requestedDate={requestedDate}
        selection={selection} onSelectionChange={setSelection} />
    </div>
  </>
}
