import { del, get, keys, set } from 'idb-keyval'

const SCHEMA_VERSION = 1
const KEY_PREFIX = `tickflow-offline:v${SCHEMA_VERSION}:`

export interface OfflineSnapshot<T> {
  schemaVersion: number
  fetchedAt: string
  revision: string | null
  data: T
}

function normalizedSnapshotPath(source: string): string {
  const url = new URL(source, window.location.origin)
  if (url.origin !== window.location.origin) {
    throw new TypeError('Offline snapshots require a same-origin API path')
  }
  url.searchParams.sort()
  return `${url.pathname}${url.search}`
}

const snapshotKey = (source: string) => `${KEY_PREFIX}${normalizedSnapshotPath(source)}`

function isCanonicalIsoTimestamp(value: unknown): value is string {
  if (typeof value !== 'string') return false
  const parsed = new Date(value)
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString() === value
}

export async function putSnapshot<T>(
  source: string,
  data: T,
  revision: string | null,
): Promise<void> {
  const snapshot: OfflineSnapshot<T> = {
    schemaVersion: SCHEMA_VERSION,
    fetchedAt: new Date().toISOString(),
    revision,
    data,
  }
  await set(snapshotKey(source), snapshot)
}

export async function getSnapshot<T>(source: string): Promise<OfflineSnapshot<T> | null> {
  const key = snapshotKey(source)
  const value = await get<OfflineSnapshot<T>>(key)
  if (!value) return null

  if (
    value.schemaVersion !== SCHEMA_VERSION ||
    !isCanonicalIsoTimestamp(value.fetchedAt)
  ) {
    await del(key)
    return null
  }

  return value
}

export async function clearSnapshot(source: string): Promise<void> {
  await del(snapshotKey(source))
}

export async function clearAllSnapshots(): Promise<void> {
  const storedKeys = await keys()
  await Promise.all(
    storedKeys
      .filter((key): key is string => typeof key === 'string' && key.startsWith(KEY_PREFIX))
      .map((key) => del(key)),
  )
}
