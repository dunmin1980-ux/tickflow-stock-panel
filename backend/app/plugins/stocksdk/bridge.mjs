#!/usr/bin/env node
/**
 * stock-sdk 桥接脚本。Python 后端通过 subprocess 调用它，用真实 stock-sdk 抓数据。
 *
 * Original implementation by @forrany (PR #57), migrated to plugin architecture.
 *
 * 协议:
 *   - stdin: 单行 JSON  { op, symbols?, adjust?, period?, start?, end?, concurrency? }
 *   - stdout: 单行 JSON
 *       daily/adj/minute: { ok:true, op, rows: { [appSymbol]: Row[] } }
 *       realtime/instruments: { ok:true, op, rows: Row[] }
 *       ping: { ok:true, op:'ping', version }
 *       失败:  { ok:false, error }
 *
 * op:
 *   daily        —— 日K(adjust 默认 none), 每个 symbol 一组 bars
 *   visual_daily —— Visual Workbench 单票日K，固定 Tencent HTTPS fallback
 *   calendar     —— A 股交易日历
 *   adj          —— 除权因子: 取 hfq 与 none 收盘价, ex_factor = close_hfq / close_none
 *   minute       —— 分钟K(period 默认 5)
 *   realtime     —— 全 A 股实时快照(batch.cn)
 *   instruments  —— 全 A 股标的维表(batch.cn 提取元数据)
 *   ping         —— 探活
 *
 * 说明: daily/adj/minute 的入参 symbols 是「app 符号」(如 600519.SH)。stock-sdk 能容错解析，
 * 返回结果里我们**回显原始 app 符号**作为 key，避免 code→符号 的歧义(指数/股票同码等)。
 * realtime/instruments 是全市场枚举，由 code + marketId 反推后缀。
 */
import { createRequire } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { execSync } from 'node:child_process'
import path from 'node:path'

/**
 * 解析 stock-sdk 入口。ESM 的 bare import 只查本地 node_modules 链，不查全局，
 * 因此这里显式在 [本地(脚本旁), 全局 npm root, NODE_PATH] 中查找后动态 import。
 * 部署时优先用脚本旁 vendored 的 node_modules/stock-sdk。
 */
async function loadSDK() {
  const require = createRequire(import.meta.url)
  const scriptDir = path.dirname(fileURLToPath(import.meta.url))
  // 候选 node_modules 目录（按优先级）
  const nmDirs = [path.join(scriptDir, 'node_modules')]
  for (const p of (process.env.NODE_PATH || '').split(path.delimiter).filter(Boolean)) nmDirs.push(p)
  try {
    const groot = execSync('npm root -g', { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim()
    if (groot) nmDirs.push(groot)
  } catch {
    /* npm 不可用则忽略 */
  }
  let entry
  for (const nm of nmDirs) {
    try {
      entry = require.resolve(path.join(nm, 'stock-sdk'))
      break
    } catch {
      /* 试下一个 */
    }
  }
  if (!entry) {
    throw new Error(
      `无法解析 stock-sdk（已搜索: ${nmDirs.join(', ')}）。请在桥接目录 npm install，或全局 npm i -g stock-sdk。`
    )
  }
  const mod = await import(pathToFileURL(entry).href)
  return mod.StockSDK || (mod.default && mod.default.StockSDK)
}

const MARKET_ID_TO_SUFFIX = { '1': 'SH', '51': 'SZ', '62': 'BJ' }

// 下游(Python)可能提前关闭管道，忽略 EPIPE 避免噪声崩溃。
process.stdout.on('error', (e) => {
  if (e && e.code === 'EPIPE') process.exit(0)
})

function readStdin() {
  return new Promise((resolve, reject) => {
    let buf = ''
    process.stdin.setEncoding('utf8')
    process.stdin.on('data', (c) => (buf += c))
    process.stdin.on('end', () => resolve(buf))
    process.stdin.on('error', reject)
  })
}

/** 简单并发池: 对 items 逐个跑 worker，最多 concurrency 个在飞。 */
async function mapPool(items, concurrency, worker) {
  const results = new Array(items.length)
  let next = 0
  const runners = new Array(Math.min(concurrency, items.length)).fill(0).map(async () => {
    while (true) {
      const i = next++
      if (i >= items.length) return
      try {
        results[i] = await worker(items[i], i)
      } catch (e) {
        results[i] = { __error: String((e && e.message) || e) }
      }
    }
  })
  await runners.reduce((p) => p, Promise.resolve())
  await Promise.all(runners)
  return results
}

/** code + marketId → app 符号(600519.SH)。反推失败则退化用 code 前缀猜测。 */
function toAppSymbol(code, marketId) {
  const suffix = MARKET_ID_TO_SUFFIX[String(marketId)] || guessSuffix(code)
  return suffix ? `${code}.${suffix}` : String(code)
}

function guessSuffix(code) {
  const c = String(code)
  if (/^(6|5|9)/.test(c)) return 'SH'
  if (/^(0|3|1|2)/.test(c)) return 'SZ'
  if (/^(4|8|92)/.test(c)) return 'BJ'
  return ''
}

function toTencentSymbol(symbol) {
  const value = String(symbol).trim()
  const match = value.match(/^(\d{6})(?:\.(SZ|SH|BJ))?$/i)
  if (!match) throw new Error(`unsupported A-share symbol: ${value}`)
  const code = match[1]
  const suffix = (match[2] || guessSuffix(code)).toLowerCase()
  if (!['sz', 'sh', 'bj'].includes(suffix)) {
    throw new Error(`unsupported A-share market: ${value}`)
  }
  return `${suffix}${code}`
}

function parseTencentAssignment(text) {
  const start = text.indexOf('{')
  if (start < 0) throw new Error('Tencent kline response has no JSON object')
  return JSON.parse(text.slice(start))
}

async function fetchTencentDaily(sym, { adjust, start, end, count = 600 }) {
  const tencentSymbol = toTencentSymbol(sym)
  const normalizedAdjust = normAdjust(adjust)
  const suffix = normalizedAdjust === 'qfq' ? 'qfq' : normalizedAdjust === 'hfq' ? 'hfq' : ''
  const key = suffix ? `${suffix}day` : 'day'
  const params = `${tencentSymbol},day,,,${count},${suffix || 'none'}`
  const url = `https://ifzq.gtimg.cn/appstock/app/fqkline/get?_var=tickflow_visual&param=${encodeURIComponent(params)}`
  const response = await fetch(url, {
    headers: {
      Accept: 'application/json,text/plain,*/*',
      'User-Agent': 'tickflow-stock-panel/visual-v1',
    },
    signal: AbortSignal.timeout(20000),
  })
  if (!response.ok) throw new Error(`Tencent kline HTTP ${response.status}`)
  const payload = parseTencentAssignment(await response.text())
  if (payload.code !== 0) throw new Error(`Tencent kline error: ${payload.msg || payload.code}`)
  const document = payload.data && payload.data[tencentSymbol]
  const bars = document && document[key]
  if (!Array.isArray(bars)) throw new Error(`Tencent kline dataset missing: ${key}`)
  const quote = document.qt && document.qt[tencentSymbol]
  const quoteDate = Array.isArray(quote) && quote[30] ? String(quote[30]).slice(0, 8) : ''
  const quoteAmount = Array.isArray(quote) && quote[35]
    ? Number(String(quote[35]).split('/')[2])
    : null
  const compactStart = start ? String(start).replaceAll('-', '') : null
  const compactEnd = end ? String(end).replaceAll('-', '') : null
  return bars
    .map((bar) => {
      const compactDate = String(bar[0]).replaceAll('-', '')
      return {
        date: String(bar[0]),
        open: Number(bar[1]),
        close: Number(bar[2]),
        high: Number(bar[3]),
        low: Number(bar[4]),
        volume: Number(bar[5]),
        amount: compactDate === quoteDate && Number.isFinite(quoteAmount) ? quoteAmount : null,
      }
    })
    .filter((bar) => {
      const compactDate = bar.date.replaceAll('-', '')
      return (!compactStart || compactDate >= compactStart) && (!compactEnd || compactDate <= compactEnd)
    })
}

/** stock-sdk 的 adjust 取值是 '' | 'qfq' | 'hfq'（无 'none'）。这里做兼容映射。 */
function normAdjust(v) {
  if (v === 'hfq') return 'hfq'
  if (v === 'qfq') return 'qfq'
  return '' // none / undefined / 空 → 不复权
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

/**
 * 上游(东财)偶发冷启动/限流时对活跃标的返回空数组(非报错)。对已知应有数据的请求，
 * 空结果重试若干次以提升鲁棒性；真正无数据(退市/停牌/区间无交易)时多花几次调用可接受。
 */
async function fetchWithRetry(fn, { retries = 2, delayMs = 300 } = {}) {
  let last = []
  for (let i = 0; i <= retries; i++) {
    const r = await fn()
    if (Array.isArray(r) && r.length > 0) return r
    last = Array.isArray(r) ? r : []
    if (i < retries) await sleep(delayMs)
  }
  return last
}

async function fetchDaily(sdk, sym, { adjust, period = 'daily', start, end }) {
  const opts = { period, adjust: normAdjust(adjust) }
  if (start) opts.startDate = start
  if (end) opts.endDate = end
  return fetchWithRetry(() => sdk.kline.cn(sym, opts))
}

async function opDaily(sdk, job) {
  const { symbols = [], adjust = 'none', period = 'daily', start, end, concurrency = 6 } = job
  const out = {}
  const rows = await mapPool(symbols, concurrency, (sym) =>
    fetchDaily(sdk, sym, { adjust, period, start, end })
  )
  symbols.forEach((sym, i) => {
    const r = rows[i]
    out[sym] = Array.isArray(r) ? r : []
  })
  return out
}

async function opVisualDaily(job) {
  const { symbols = [], adjust = 'none', start, end } = job
  if (symbols.length !== 1) throw new Error('visual_daily requires exactly one symbol')
  const symbol = symbols[0]
  return { [symbol]: await fetchTencentDaily(symbol, { adjust, start, end }) }
}

async function opCalendar(sdk, job) {
  const { start, end } = job
  const dates = await sdk.reference.tradingCalendar()
  return (Array.isArray(dates) ? dates : []).filter((value) => {
    const compact = String(value).replaceAll('-', '')
    return (!start || compact >= String(start)) && (!end || compact <= String(end))
  })
}

async function opAdj(sdk, job) {
  const { symbols = [], start, end, concurrency = 6 } = job
  const out = {}
  await mapPool(symbols, concurrency, async (sym) => {
    const [none, hfq] = await Promise.all([
      fetchDaily(sdk, sym, { adjust: 'none', start, end }),
      fetchDaily(sdk, sym, { adjust: 'hfq', start, end }),
    ])
    const noneByDate = new Map()
    for (const b of none) if (b && b.close) noneByDate.set(b.date, b.close)
    const factors = []
    for (const b of hfq) {
      if (!b || !b.date) continue
      const rawClose = noneByDate.get(b.date)
      if (!rawClose || !b.close) continue
      factors.push({ symbol: sym, trade_date: b.date, ex_factor: b.close / rawClose })
    }
    out[sym] = factors
    return factors
  })
  return out
}

async function opMinute(sdk, job) {
  const { symbols = [], period = 5, start, end, concurrency = 6 } = job
  const out = {}
  await mapPool(symbols, concurrency, async (sym) => {
    const opts = { period: String(period) }
    if (start) opts.startDate = start
    if (end) opts.endDate = end
    const bars = await fetchWithRetry(() => sdk.kline.cnMinute(sym, opts))
    out[sym] = Array.isArray(bars) ? bars : []
    return out[sym]
  })
  return out
}

async function opRealtime(sdk, job) {
  const { concurrency = 8 } = job
  const all = await sdk.batch.cn({ concurrency })
  const rows = []
  for (const q of all || []) {
    if (!q || !q.code) continue
    rows.push({
      symbol: toAppSymbol(q.code, q.marketId),
      name: q.name,
      last_price: q.price,
      prev_close: q.prevClose,
      open: q.open,
      high: q.high,
      low: q.low,
      volume: q.volume,
      amount: q.amount,
      change_pct: q.changePercent,
    })
  }
  return rows
}

async function opInstruments(sdk, job) {
  const { concurrency = 8 } = job
  const all = await sdk.batch.cn({ concurrency })
  const rows = []
  for (const q of all || []) {
    if (!q || !q.code) continue
    const suffix = MARKET_ID_TO_SUFFIX[String(q.marketId)] || guessSuffix(q.code)
    // 形状对齐 tickflow 的 Instrument(数值扩展字段放 ext),以复用 instrument_sync 的 flatten。
    rows.push({
      symbol: toAppSymbol(q.code, q.marketId),
      name: q.name,
      code: String(q.code),
      exchange: suffix,
      region: 'CN',
      type: 'stock',
      ext: {
        total_shares: q.totalShares ?? null,
        float_shares: q.circulatingShares ?? null,
        limit_up: q.limitUp ?? null,
        limit_down: q.limitDown ?? null,
      },
    })
  }
  return rows
}

async function main() {
  let job
  try {
    const raw = (await readStdin()).trim()
    job = raw ? JSON.parse(raw) : {}
  } catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: `invalid job json: ${e.message}` }))
    return
  }
  const op = job.op || 'ping'
  try {
    const StockSDK = await loadSDK()
    if (!StockSDK) throw new Error('stock-sdk 已解析但未导出 StockSDK')
    if (op === 'ping') {
      process.stdout.write(JSON.stringify({ ok: true, op: 'ping', version: StockSDK.version || 'ok' }))
      return
    }
    const sdk = new StockSDK({ retry: { maxRetries: 3, baseDelay: 400 } })
    let rows
    switch (op) {
      case 'daily':
        rows = await opDaily(sdk, job)
        break
      case 'visual_daily':
        rows = await opVisualDaily(job)
        break
      case 'calendar':
        rows = await opCalendar(sdk, job)
        break
      case 'adj':
        rows = await opAdj(sdk, job)
        break
      case 'minute':
        rows = await opMinute(sdk, job)
        break
      case 'realtime':
        rows = await opRealtime(sdk, job)
        break
      case 'instruments':
        rows = await opInstruments(sdk, job)
        break
      default:
        process.stdout.write(JSON.stringify({ ok: false, error: `unknown op: ${op}` }))
        return
    }
    process.stdout.write(JSON.stringify({ ok: true, op, rows }))
  } catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, op, error: String((e && e.stack) || e) }))
  }
}

main()
