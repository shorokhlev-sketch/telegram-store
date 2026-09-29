/**
 * Catalog meta shared by the palette, the global nav and the list: global tag
 * groups, the total, the palette data (`/api/menu`) and the seller's plain
 * chat link. Module-level cache, one fetch per session; components subscribe
 * through `useMeta`.
 */
import { useEffect, useState } from 'react'
import { fetchItems, fetchMenu, fetchTags, type MenuCategory, type TagGroup } from './api'
import { NO_FILTERS, sellerBase } from './lib'

export interface Meta {
  groups: TagGroup[] | null
  total: number | null
  /** Palette sections in fixed order (only those with active lots). */
  menu: MenuCategory[] | null
  seller: string | null
  error: boolean
}

let meta: Meta = { groups: null, total: null, menu: null, seller: null, error: false }
let loading: Promise<void> | null = null
const listeners = new Set<() => void>()

function emit(next: Partial<Meta>) {
  meta = { ...meta, ...next }
  listeners.forEach((l) => l())
}

export function loadMeta(force = false): Promise<void> {
  if (loading && !force) return loading
  emit({ error: false })
  loading = Promise.all([fetchTags(), fetchItems(NO_FILTERS, null, undefined, 1), fetchMenu().catch((): MenuCategory[] => [])])
    .then(([groups, first, menu]) => {
      emit({ groups, menu, total: first.total, seller: meta.seller ?? sellerBase(first.items[0]?.seller_url) })
    })
    .catch(() => {
      loading = null
      emit({ error: true })
    })
  return loading
}

/** Remember the seller link from any lot we've seen (menus show a plain chat link). */
export function noteSeller(url: string | null | undefined) {
  if (!meta.seller && url) emit({ seller: sellerBase(url) })
}

export function useMeta(): Meta {
  const [, force] = useState(0)
  useEffect(() => {
    const l = () => force((n) => n + 1)
    listeners.add(l)
    void loadMeta()
    return () => {
      listeners.delete(l)
    }
  }, [])
  return meta
}
