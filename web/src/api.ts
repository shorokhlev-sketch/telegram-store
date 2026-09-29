/** Typed client for the public storefront API (same origin, under BASE_URL). */
import type { Filters } from './lib'

export interface ItemCard {
  public_id: string
  title: string
  device_type: string | null
  brand: string | null
  model: string | null
  storage_gb: number | null
  variant: string | null
  color: string | null
  battery_health: number | null
  grade: string | null
  price: number | null
  cover: string | null
  has_video: boolean
  published_at: string | null
  /** Full note text (untruncated) — the UI clips it, the API doesn't. */
  note: string | null
  grade_label: string | null
  seller_url: string | null
}

export interface ItemsPage {
  items: ItemCard[]
  next_cursor: string | null
  /** Active items matching the same tag filter, ignoring cursor/limit. */
  total: number
}

export interface MediaItem {
  kind: 'photo' | 'video'
  preview?: string | null
  full?: string | null
  video?: string | null
  duration?: number | null
}

export interface ItemDetail extends ItemCard {
  year: number | null
  kit: { code: string; label: string }[]
  grade_description: string | null
  media: MediaItem[]
  miniapp_url: string
}

export interface Tag {
  slug: string
  label: string
  count: number
}

export interface TagGroup {
  kind: string
  label: string
  tags: Tag[]
}

export interface PriceHistogram {
  min: number | null
  max: number | null
  buckets: { from: number; to: number; count: number }[]
}

export interface LeadResult {
  ok: boolean
  message: string
}

export type LeadBody = { phone?: string } | { name: string; contact: string; website: string }

const API = `${import.meta.env.BASE_URL}api`

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : `HTTP ${status}`)
    this.status = status
    this.detail = detail
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${API}${path}`, { ...init, headers: { Accept: 'application/json', ...init?.headers } })
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e
    throw new ApiError(0, 'Нет соединения')
  }
  if (!res.ok) {
    let detail: unknown = null
    try {
      detail = (await res.json())?.detail ?? null
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}

/** Query for tags + free price range (shared by the list, count and facets). */
function filterParams(f: Filters, q = new URLSearchParams()): URLSearchParams {
  if (f.tags.length) q.set('tags', f.tags.join(','))
  if (f.pmin != null) q.set('price_min', String(f.pmin))
  if (f.pmax != null) q.set('price_max', String(f.pmax))
  return q
}

export function fetchItems(f: Filters, cursor: string | null, signal?: AbortSignal, limit = 20) {
  const q = filterParams(f, new URLSearchParams({ limit: String(limit) }))
  if (cursor) q.set('cursor', cursor)
  return request<ItemsPage>(`/items?${q}`, { signal })
}

/** Cheap COUNT for the filter sheet preview («Показать N предложений»). */
export function countItems(f: Filters, signal?: AbortSignal): Promise<number> {
  return request<{ total: number }>(`/items/count?${filterParams(f)}`, { signal }).then((r) => r.total)
}

/** Price distribution of lots matching the other filters (price tags ignored). */
export function fetchPriceHistogram(tags: string[], signal?: AbortSignal) {
  const q = new URLSearchParams()
  const own = tags.filter((t) => !t.startsWith('price:'))
  if (own.length) q.set('tags', own.join(','))
  return request<PriceHistogram>(`/items/price-histogram?${q}`, { signal })
}

export function fetchItem(publicId: string, signal?: AbortSignal) {
  return request<ItemDetail>(`/items/${encodeURIComponent(publicId)}`, { signal })
}

/**
 * Tag groups with counts. Without filters: plain counts (zero omitted). With
 * filters: faceted counts (each kind counted against the other kinds), every
 * value that has an active lot is kept, so the UI dims zeros instead of hiding.
 */
export function fetchTags(f?: Filters, signal?: AbortSignal) {
  const q = f ? filterParams(f) : new URLSearchParams()
  const s = q.toString()
  return request<TagGroup[]>(`/tags${s ? `?${s}` : ''}`, { signal })
}

/** `GET /api/menu`: catalog palette data (active lots only). */
export interface MenuModel {
  /** Equals the `model:*` tag slug the filters use. */
  slug: string
  label: string
  count: number
  price_min: number | null
  price_max: number | null
  storages: number[]
  variants: string[]
  grades: Record<string, number>
  /** Newest lot of the model: cover photo, device and colour (silhouette fallback). */
  device_type: string | null
  color: string | null
  cover: string | null
}

export interface MenuCategory {
  /** iphone | ipad | macbook | watch | airpods | other */
  code: string
  label: string
  device_type: string | null
  count: number
  models: MenuModel[]
  grades: Record<string, number>
}

export function fetchMenu(signal?: AbortSignal) {
  return request<{ categories: MenuCategory[] }>('/menu', { signal }).then((r) => r.categories)
}

export function postLead(publicId: string, body: LeadBody, initData?: string) {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (initData) headers['X-Telegram-Init-Data'] = initData
  return request<LeadResult>(`/items/${encodeURIComponent(publicId)}/lead`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  })
}

// ---------------------------------------------------------------------------
// Admin (AdminPage, Mini App only, X-Telegram-Init-Data required)
// ---------------------------------------------------------------------------

export interface AdminMe {
  ok: boolean
  user_id: number
}

export interface AdminPhoto {
  file_unique_id: string
  preview: string
}

export interface AdminItem {
  public_id: string
  title: string
  price: number | null
  note: string | null
  status: 'active' | 'sold' | 'draft'
  published_at: string | null
  sold_at: string | null
  cover: string | null
  lead_count: number
  /** Catalog category code or "other" (manual entry). */
  category: string
  device_type: string | null
  brand: string | null
  model: string | null
  year: number | null
  storage_gb: number | null
  variant: string | null
  color: string | null
  battery_health: number | null
  grade: string | null
  kit: string[]
  /** Photos only, display order: the first one is the cover. */
  photos: AdminPhoto[]
}

/** Fields `PATCH /api/admin/items/{id}` accepts; send only what changed. */
export interface AdminPatch {
  category?: string
  device_type?: string | null
  brand?: string | null
  model?: string | null
  year?: number | null
  storage_gb?: number | null
  variant?: string | null
  color?: string | null
  battery_health?: number | null
  grade?: string
  kit?: string[]
  price?: number
  note?: string
}

/** 422 body of the admin edit endpoints: one field, one Russian message. */
export interface AdminFieldError {
  field: string
  message: string
}

export function adminFieldError(e: unknown): AdminFieldError | null {
  if (!(e instanceof ApiError) || e.status !== 422) return null
  const d = e.detail as Partial<AdminFieldError> | null
  return d && typeof d === 'object' && typeof d.field === 'string' && typeof d.message === 'string' ? { field: d.field, message: d.message } : null
}

export interface AdminCategory {
  code: string
  label: string
  device_type: string | null
  variant_label: string | null
  has_storage: boolean
  battery_skippable: boolean
  /** «Другое»: typed brand/model/year instead of catalog pickers. */
  manual: boolean
}

export interface AdminModel {
  name: string
  storages: number[]
  variants: string[]
  colors: string[]
}

export interface AdminCatalog {
  categories: AdminCategory[]
  models: Record<string, AdminModel[]>
  device_types: { code: string; label: string }[]
  kit: { code: string; label: string }[]
  grades: { code: string; label: string; description: string }[]
  other_storages: number[]
  limits: { text_max: number; note_max: number; price_max: number; year_min: number; year_max: number }
}

export interface AdminSoldResult {
  public_id: string
  already_sold: boolean
  failed_message_ids: number[]
}

export interface AdminPatchResult extends AdminItem {
  channel_updated: boolean
}

function adminHeaders(initData: string): Record<string, string> {
  return { 'X-Telegram-Init-Data': initData }
}

export function fetchAdminMe(initData: string, signal?: AbortSignal) {
  return request<AdminMe>('/admin/me', { headers: adminHeaders(initData), signal })
}

export function fetchAdminItems(status: 'active' | 'sold', initData: string, signal?: AbortSignal) {
  return request<AdminItem[]>(`/admin/items?status=${status}`, { headers: adminHeaders(initData), signal })
}

export function postAdminSold(publicId: string, initData: string) {
  return request<AdminSoldResult>(`/admin/items/${encodeURIComponent(publicId)}/sold`, {
    method: 'POST',
    headers: adminHeaders(initData),
  })
}

export function patchAdminItem(publicId: string, body: AdminPatch, initData: string) {
  return request<AdminPatchResult>(`/admin/items/${encodeURIComponent(publicId)}`, {
    method: 'PATCH',
    headers: { ...adminHeaders(initData), 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

/** Make one of the lot's photos the cover; the channel post swaps its photo. */
export function postAdminCover(publicId: string, fileUniqueId: string, initData: string) {
  return request<AdminPatchResult>(`/admin/items/${encodeURIComponent(publicId)}/cover`, {
    method: 'POST',
    headers: { ...adminHeaders(initData), 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_unique_id: fileUniqueId }),
  })
}

let catalogCache: Promise<AdminCatalog> | null = null

/** Reference data for the edit form; fetched once per session (static per deploy). */
export function fetchAdminCatalog(initData: string): Promise<AdminCatalog> {
  if (!catalogCache) {
    catalogCache = request<AdminCatalog>('/admin/catalog', { headers: adminHeaders(initData) })
    catalogCache.catch(() => {
      catalogCache = null
    })
  }
  return catalogCache
}
