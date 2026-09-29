/**
 * Session cache of full lot details, keyed by public_id.
 *
 * List responses now carry everything a card needs (date, note, seller
 * link, condition label), so cards render straight from list data — no
 * per-card detail fetch. This module is left only for ItemPage: a card
 * seeds this cache with its own data before navigating (see ItemCard.tsx),
 * so the item page paints instantly, then ItemPage fetches the real detail
 * (gallery, kit, year…) and overwrites the seed via `rememberDetail`.
 */
import type { ItemDetail } from './api'

const cache = new Map<string, ItemDetail>()

export function cachedDetail(publicId: string): ItemDetail | undefined {
  return cache.get(publicId)
}

export function rememberDetail(item: ItemDetail) {
  cache.set(item.public_id, item)
}
