import { useEffect, useState } from 'react'
import type { NavigateFunction } from 'react-router-dom'
import type { ItemCard, TagGroup } from './api'

/** Shop name: top bar, menu, titles. Set VITE_SHOP_NAME at build time. */
export const SHOP_NAME: string = (import.meta.env.VITE_SHOP_NAME as string | undefined)?.trim() || 'Apple Resale'

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

const nf = new Intl.NumberFormat('ru-RU')

/** 69000 → "69 000 ₽" (non-breaking spaces, so a price never wraps). */
export function formatPrice(price: number | null | undefined): string {
  if (price == null) return 'Цена по запросу'
  return `${nf.format(price).replace(/\s/g, ' ')} ₽`
}

/** 60000 → "60", 62500 → "62,5" (thousands, for ticks and pill labels). */
export function thousands(n: number): string {
  const k = n / 1000
  return Number.isInteger(k) ? String(k) : k.toFixed(1).replace('.', ',')
}

export function formatDuration(sec: number | null | undefined): string {
  if (!sec || sec <= 0) return ''
  const s = Math.round(sec)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/** API datetimes are UTC; SQLite drops the offset, so a bare ISO string is UTC too. */
export function parseApiDate(iso: string | null | undefined): Date | null {
  if (!iso) return null
  const withTz = /(Z|[+-]\d\d:?\d\d)$/.test(iso) ? iso : `${iso}Z`
  const d = new Date(withTz)
  return Number.isNaN(d.getTime()) ? null : d
}

/** «24 сентября» (with the year only when it isn't this year). */
export function dateLong(iso: string | null | undefined, now = new Date()): string {
  const d = parseApiDate(iso)
  if (!d) return ''
  return d
    .toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', ...(d.getFullYear() !== now.getFullYear() ? { year: 'numeric' } : {}) })
    .replace(/\s*г\.$/, '')
}

/** «сегодня», «вчера», else «24 сентября». */
export function dateRel(iso: string | null | undefined, now = new Date()): string {
  const d = parseApiDate(iso)
  if (!d) return ''
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const diff = Math.round((day(now) - day(d)) / 86_400_000)
  if (diff === 0) return 'сегодня'
  if (diff === 1) return 'вчера'
  return dateLong(iso, now)
}

/** Russian plural: plural(5, ['предложение', 'предложения', 'предложений']). */
export function plural(n: number, forms: [string, string, string]): string {
  const m10 = n % 10
  const m100 = n % 100
  if (m10 === 1 && m100 !== 11) return forms[0]
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return forms[1]
  return forms[2]
}

export function formatCount(n: number): string {
  return nf.format(n).replace(/\s/g, ' ')
}

export function offers(n: number): string {
  return `${formatCount(n)} ${plural(n, ['предложение', 'предложения', 'предложений'])}`
}

/** «2 из 12 предложений» (genitive after «из»: 1 → «предложения», else «предложений»). */
export function offersOf(n: number, of: number): string {
  const one = of % 10 === 1 && of % 100 !== 11
  return `${formatCount(n)} из ${formatCount(of)} ${one ? 'предложения' : 'предложений'}`
}

export function storageLabel(gb: number | null | undefined): string | null {
  if (!gb) return null
  return gb >= 1024 && gb % 1024 === 0 ? `${gb / 1024} ТБ` : `${gb} ГБ`
}

// ---------------------------------------------------------------------------
// Vocabulary (codes → words). Tag labels from the API are bot-oriented
// («Состояние B», «АКБ 90%+»); the storefront shows its own words.
// ---------------------------------------------------------------------------

export const DEVICE: Record<string, string> = {
  phone: 'Телефон',
  laptop: 'Ноутбук',
  tablet: 'Планшет',
  watch: 'Часы',
  headphones: 'Наушники',
  other: 'Другое',
}
export const DEVICE_PL: Record<string, string> = {
  phone: 'Телефоны',
  laptop: 'Ноутбуки',
  tablet: 'Планшеты',
  watch: 'Часы',
  headphones: 'Наушники',
  other: 'Другое',
}
export const DEVICE_ORDER = ['phone', 'laptop', 'tablet', 'watch', 'headphones']

export const GRADE: Record<string, { short: string; seg: string; long: string }> = {
  A: { short: 'Идеал', seg: 'Идеал', long: 'Без следов использования, как новый' },
  B: { short: 'Хорошее', seg: 'Хорошее', long: 'Мелкие потёртости, царапины не видны на расстоянии' },
  C: { short: 'Заметные следы', seg: 'Следы', long: 'Заметные царапины или сколы, всё работает' },
}
export const GRADE_ORDER = ['A', 'B', 'C']

export const KIT: [string, string][] = [
  ['box', 'Коробка'],
  ['cable', 'Кабель'],
  ['adapter', 'Адаптер'],
  ['receipt', 'Чек'],
]
export const BATT: [string, string][] = [
  ['100', '100%'],
  ['90plus', '90–99%'],
  ['85plus', '85–89%'],
  ['below85', 'Ниже 85%'],
]
/** Price bands of the `price:*` tags, as ranges for the free price filter (max inclusive). */
export const PRICE_BANDS: { code: string; label: string; min: number | null; max: number | null }[] = [
  { code: '0-30k', label: 'До 30 тыс.', min: null, max: 29_999 },
  { code: '30-60k', label: '30–60 тыс.', min: 30_000, max: 59_999 },
  { code: '60-100k', label: '60–100 тыс.', min: 60_000, max: 99_999 },
  { code: '100k-plus', label: 'От 100 тыс.', min: 100_000, max: null },
]

export function gradeWord(grade: string | null | undefined): string | null {
  return grade ? (GRADE[grade]?.short ?? grade) : null
}

/** Storefront label for a tag slug (falls back to the API label). */
export function tagLabel(slug: string, apiLabel: string, plural = false): string {
  const [kind, value = ''] = slug.split(':')
  if (kind === 'device') return (plural ? DEVICE_PL : DEVICE)[value] ?? apiLabel
  if (kind === 'grade') return GRADE[value.toUpperCase()]?.short ?? apiLabel
  if (kind === 'battery') return BATT.find((b) => b[0] === value)?.[1] ?? apiLabel
  if (kind === 'price') return PRICE_BANDS.find((b) => b.code === value)?.label ?? apiLabel
  return apiLabel
}

/** Swatch for a colour name (the catalog has names, not hex). Null = no dot. */
const COLOR_HEX: [RegExp, string][] = [
  [/natural/i, '#BDB6AA'],
  [/desert/i, '#C9B08F'],
  [/space\s*gr[ae]y|graphite|grey|gray/i, '#7D7E80'],
  [/black|midnight|jet|obsidian|onyx|чёрн|черн/i, '#2B2B2D'],
  [/sky|light\s*blue|mist/i, '#B8C6D5'],
  [/ultramarine/i, '#4A5DC7'],
  [/teal/i, '#7FB7B0'],
  [/blue|синий|голуб/i, '#4E6C85'],
  [/mint|sage|green|зел/i, '#CFE2D5'],
  [/cream|beige/i, '#EAE3D3'],
  [/starlight/i, '#EFE9DF'],
  [/gold|золот/i, '#E8D6B8'],
  [/rose|pink|розов/i, '#F2C8C8'],
  [/purple|lavender|violet|фиол/i, '#CFC5E0'],
  [/red|красн/i, '#B3262E'],
  [/yellow|жёлт|желт/i, '#F4E08A'],
  [/orange|оранж/i, '#F08A3C'],
  [/silver|серебр/i, '#E3E4E5'],
  [/titanium/i, '#9A9A98'],
  [/white|cloud|бел/i, '#F7F7F5'],
]
export function colorHex(name: string | null | undefined): string | null {
  if (!name) return null
  return COLOR_HEX.find(([re]) => re.test(name))?.[1] ?? null
}

// ---------------------------------------------------------------------------
// Lot text
// ---------------------------------------------------------------------------

/** «8/256 ГБ» for laptops, «256 ГБ», else the variant («45 мм»). */
export function memStr(it: Pick<ItemCard, 'device_type' | 'storage_gb' | 'variant'>): string {
  const st = storageLabel(it.storage_gb)
  if (it.device_type === 'laptop' && it.variant && it.storage_gb) {
    const ram = parseInt(it.variant, 10)
    if (!Number.isNaN(ram)) return `${ram}/${it.storage_gb} ГБ`
  }
  return st ?? it.variant ?? ''
}

/** Grey spec line under a tile: «256 ГБ · Идеал · АКБ 89%». */
export function specLine(it: ItemCard): string {
  const p = [memStr(it), gradeWord(it.grade)]
  if (it.battery_health != null) p.push(`АКБ ${it.battery_health}%`)
  return p.filter(Boolean).join(' · ')
}

/** Two-tone headline: [«iPhone 15 Pro.», «256 ГБ, Natural Titanium.»]. */
export function headParts(it: ItemCard): [string, string] {
  const model = it.model || it.title
  const bits: string[] = []
  const st = storageLabel(it.storage_gb)
  if (it.device_type === 'laptop' && it.variant) {
    bits.push(`${it.variant} памяти`)
    if (st) bits.push(`${st} SSD`)
  } else {
    if (st) bits.push(st)
    if (it.variant) bits.push(it.variant)
  }
  if (it.color) bits.push(it.color)
  return [`${model}.`, bits.length ? `${bits.join(', ')}.` : '']
}

/** Keep numbers glued to their units: "512 ГБ", "45 мм". */
export function tidyTitle(title: string): string {
  return title.replace(/(\d)\s+(ГБ|ТБ|мм|MB|GB|TB)/g, '$1 $2')
}

/** https://t.me/seller?text=… → https://t.me/seller (plain chat link for menus). */
export function sellerBase(url: string | null | undefined): string | null {
  if (!url) return null
  const i = url.indexOf('?')
  return i > 0 ? url.slice(0, i) : url
}

// ---------------------------------------------------------------------------
// Filters: tags (AND across kinds, OR inside) + free price range, in the URL
// as ?tags=a,b&price_min=…&price_max=…
// ---------------------------------------------------------------------------

export interface Filters {
  tags: string[]
  pmin: number | null
  pmax: number | null
}

export const NO_FILTERS: Filters = { tags: [], pmin: null, pmax: null }

function intParam(v: string | null): number | null {
  if (!v || !/^\d+$/.test(v)) return null
  return Number(v)
}

export function parseFilters(params: URLSearchParams): Filters {
  const raw = params.get('tags')
  const tags = raw ? Array.from(new Set(raw.split(',').map((s) => s.trim()).filter(Boolean))).sort() : []
  return { tags, pmin: intParam(params.get('price_min')), pmax: intParam(params.get('price_max')) }
}

export function filtersQuery(f: Filters): string {
  const q = new URLSearchParams()
  if (f.tags.length) q.set('tags', [...f.tags].sort().join(','))
  if (f.pmin != null) q.set('price_min', String(f.pmin))
  if (f.pmax != null) q.set('price_max', String(f.pmax))
  const s = q.toString()
  return s ? `?${s}` : ''
}

export function filtersKey(f: Filters): string {
  return `${[...f.tags].sort().join(',')}|${f.pmin ?? ''}|${f.pmax ?? ''}`
}

export function filterCount(f: Filters): number {
  return f.tags.length + (f.pmin != null || f.pmax != null ? 1 : 0)
}

export function tagsOfKind(f: Filters, prefix: string): string[] {
  return f.tags.filter((t) => t.startsWith(`${prefix}:`))
}

export function toggleTag(f: Filters, slug: string): Filters {
  return { ...f, tags: f.tags.includes(slug) ? f.tags.filter((t) => t !== slug) : [...f.tags, slug].sort() }
}

export function setKind(f: Filters, prefix: string, slugs: string[]): Filters {
  return { ...f, tags: [...f.tags.filter((t) => !t.startsWith(`${prefix}:`)), ...slugs].sort() }
}

/** Toggling a model tag off, with no model left selected, drops colour picks too (a colour means nothing without a model). */
export function toggleModelTag(f: Filters, slug: string): Filters {
  const turningOff = f.tags.includes(slug)
  const next = toggleTag(f, slug)
  return turningOff && !tagsOfKind(next, 'model').length ? setKind(next, 'color', []) : next
}

/** The price band whose range equals the current price filter, if any. */
export function activeBand(f: Filters): string | null {
  if (f.pmin == null && f.pmax == null) return null
  return PRICE_BANDS.find((b) => b.min === f.pmin && b.max === f.pmax)?.code ?? null
}

/** «до 30 тыс.», «30–60 тыс.», «от 100 тыс.» for the price pill. */
export function priceRangeLabel(pmin: number | null, pmax: number | null): string {
  // Bands store 59 999 as the max: show it as «60».
  const k = (n: number) => thousands(n % 1000 === 999 ? n + 1 : n)
  if (pmin == null && pmax != null) return `до ${k(pmax)} тыс.`
  if (pmin != null && pmax == null) return `от ${k(pmin)} тыс.`
  if (pmin != null && pmax != null) return `${k(pmin)}–${k(pmax)} тыс.`
  return 'Цена'
}

// ---------------------------------------------------------------------------
// Categories (catalog palette, breadcrumbs): Apple lines are device +
// brand:apple; «Другие устройства» = every non-Apple brand.
// ---------------------------------------------------------------------------

export interface Category {
  k: string
  /** `items.category` code in `/api/menu`. */
  code: string
  name: string
  /** Segment label on phones. */
  short: string
  all: string
  device: string | null
}

export const CATS: Category[] = [
  { k: 'iphone', code: 'iphone', name: 'iPhone', short: 'iPhone', all: 'Все iPhone', device: 'phone' },
  { k: 'ipad', code: 'ipad', name: 'iPad', short: 'iPad', all: 'Все iPad', device: 'tablet' },
  { k: 'mac', code: 'macbook', name: 'Mac', short: 'Mac', all: 'Все Mac', device: 'laptop' },
  { k: 'watch', code: 'watch', name: 'Watch', short: 'Watch', all: 'Все Watch', device: 'watch' },
  { k: 'airpods', code: 'airpods', name: 'AirPods', short: 'AirPods', all: 'Все AirPods', device: 'headphones' },
  { k: 'other', code: 'other', name: 'Другие устройства', short: 'Другие', all: 'Все другие устройства', device: null },
]

export const APPLE = 'brand:apple'

export function catByCode(code: string): Category {
  return CATS.find((c) => c.code === code) ?? CATS[CATS.length - 1]
}

export function otherBrands(groups: TagGroup[] | null): string[] {
  const brand = groups?.find((g) => g.kind === 'brand')
  return (brand?.tags ?? []).map((t) => t.slug).filter((s) => s !== APPLE)
}

export function catTags(c: Category, groups: TagGroup[] | null): string[] {
  return c.device ? [`device:${c.device}`, APPLE] : otherBrands(groups)
}

/** Tag kinds the catalog palette sets (shown as removable chips on phones). */
export const CATALOG_KINDS = ['device', 'brand', 'model', 'color']

/** The category whose tags are exactly the filter's device + brand tags, else null. */
export function catOfTags(f: Filters, groups: TagGroup[] | null): Category | null {
  const own = f.tags.filter((t) => t.startsWith('device:') || t.startsWith('brand:')).sort().join(',')
  if (!own) return null
  for (const c of CATS) {
    const t = catTags(c, groups)
    if (t.length && [...t].sort().join(',') === own) return c
  }
  return null
}

/** The category these filters are exactly equal to (desktop page title), else null. */
export function catOf(f: Filters, groups: TagGroup[] | null): Category | null {
  if (f.pmin != null || f.pmax != null) return null
  if (!f.tags.every((t) => t.startsWith('device:') || t.startsWith('brand:'))) return null
  return catOfTags(f, groups)
}

/** Category of one lot (for desktop breadcrumbs). */
export function catOfItem(it: Pick<ItemCard, 'device_type' | 'brand'>): Category {
  if ((it.brand ?? '').toLowerCase() === 'apple') {
    const c = CATS.find((x) => x.device === it.device_type)
    if (c) return c
  }
  return CATS[CATS.length - 1]
}

// ---------------------------------------------------------------------------
// Misc
// ---------------------------------------------------------------------------

/** Back that works on a cold deep link too: no in-app history → go to the list. */
export function goBack(navigate: NavigateFunction) {
  const idx = (window.history.state as { idx?: number } | null)?.idx ?? 0
  if (idx > 0) navigate(-1)
  else navigate('/', { replace: true })
}

/** Desktop layout switch (≥1024). */
export function useWide(): boolean {
  const q = '(min-width: 1024px)'
  const [wide, setWide] = useState(() => window.matchMedia(q).matches)
  useEffect(() => {
    const mql = window.matchMedia(q)
    const on = () => setWide(mql.matches)
    mql.addEventListener('change', on)
    return () => mql.removeEventListener('change', on)
  }, [])
  return wide
}

/** Lock page scroll while an overlay is open (keeps the scroll position). */
export function useScrollLock(active = true) {
  useEffect(() => {
    if (!active) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = prev
    }
  }, [active])
}
