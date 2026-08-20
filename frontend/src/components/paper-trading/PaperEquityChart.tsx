import { useEffect, useMemo, useRef } from 'react'
import * as echarts from 'echarts'

import type { PaperEquityPoint } from '@/lib/api'
import { useChartTheme } from '@/lib/theme'

export function PaperEquityChart({ points }: { points: PaperEquityPoint[] }) {
  const root = useRef<HTMLDivElement>(null)
  const theme = useChartTheme()
  const option = useMemo(() => ({
    animation: false,
    grid: { left: 54, right: 18, top: 24, bottom: 34 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: theme.tooltipBg,
      borderColor: theme.tooltipBorder,
      textStyle: { color: theme.tooltipText, fontSize: 12 },
    },
    legend: {
      top: 0,
      right: 0,
      textStyle: { color: theme.text, fontSize: 10 },
    },
    xAxis: {
      type: 'category',
      data: points.map(point => point.trade_date),
      axisLabel: { color: theme.text, fontSize: 10 },
      axisLine: { lineStyle: { color: theme.border } },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLabel: {
        color: theme.text,
        fontSize: 10,
        formatter: (value: number) => `¥${Math.round(value).toLocaleString('zh-CN')}`,
      },
      splitLine: { lineStyle: { color: theme.grid } },
    },
    series: [
      {
        name: '总资产',
        type: 'line',
        data: points.map(point => Number(point.total_equity_cny)),
        symbol: points.length < 12 ? 'circle' : 'none',
        symbolSize: 6,
        lineStyle: { color: '#dc2626', width: 2 },
        itemStyle: { color: '#dc2626' },
      },
      {
        name: '现金',
        type: 'line',
        data: points.map(point => Number(point.cash_cny)),
        symbol: 'none',
        lineStyle: { color: '#2563eb', width: 1.5, type: 'dashed' },
      },
    ],
  }), [points, theme])

  useEffect(() => {
    if (!root.current) return
    const chart = echarts.init(root.current, undefined, { renderer: 'canvas' })
    chart.setOption(option)
    const resize = () => chart.resize()
    window.addEventListener('resize', resize)
    return () => {
      window.removeEventListener('resize', resize)
      chart.dispose()
    }
  }, [option])

  if (points.length === 0) {
    return (
      <div className="grid h-64 place-items-center text-xs text-muted" aria-label="权益曲线">
        完成首个模拟盘日结后显示权益曲线
      </div>
    )
  }

  return <div ref={root} className="h-64 w-full" aria-label="权益曲线" />
}
