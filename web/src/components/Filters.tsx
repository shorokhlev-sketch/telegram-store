import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent as RKeyboardEvent, type PointerEvent as RPointerEvent } from 'react'
import { countItems, fetchPriceHistogram, fetchTags, type PriceHistogram, type TagGroup } from '../api'
import {
  BATT,
  CATALOG_KINDS,
  DEVICE_ORDER,
  GRADE,
  GRADE_ORDER,
  KIT,
  NO_FILTERS,
  PRICE_BANDS,
  activeBand,
  catOfTags,
  colorHex,
  filterCount,
  filtersKey,
  formatPrice,
  offers,
  priceRangeLabel,
  setKind,
  tagLabel,
  tagsOfKind,
  toggleModelTag,
  toggleTag,
  type Filters,
} from '../lib'
import { Check, CheckS, ChevD, ChevD14, Sliders, Xp, Xs } from './Icons'
import Sheet from './Sheet'

// ---------------------------------------------------------------------------
// Facet rows: storefront order and words, counts from /api/tags (faceted
// when filters are set), zero-count values kept (dimmed), picked values that
// vanished from the data kept too (so they can be unticked).
// ---------------------------------------------------------------------------

export interface Row {
  slug: string
  label: string
  n: number
  on: boolean
}

const KIND_OF: Record<string, string> = {
  device: 'device',
  brand: 'brand',
  model: 'model',
  storage: 'storage',
  color: 'color',
  grade: 'grade',
  battery: 'battery_band',
  kit: 'kit',
  price: 'price_band',
}

const FIXED: Record<string, string[]> = {
  device: DEVICE_ORDER,
  grade: GRADE_ORDER.map((g) => g.toLowerCase()),
  battery: BATT.map((b) => b[0]),
  kit: KIT.map((k) => k[0]),
  price: PRICE_BANDS.map((b) => b.code),
}

export function facet(groups: TagGroup[] | null, prefix: string, f: Filters, pluralLabels = false): Row[] {
  const g = groups?.find((x) => x.kind === KIND_OF[prefix])
  const api = new Map((g?.tags ?? []).map((t) => [t.slug, t]))
  const slugs: string[] = []
  for (const v of FIXED[prefix] ?? []) slugs.push(`${prefix}:${v}`)
  for (const t of g?.tags ?? []) if (!slugs.includes(t.slug)) slugs.push(t.slug)
  for (const s of tagsOfKind(f, prefix)) if (!slugs.includes(s)) slugs.push(s)
  return slugs.map((slug) => {
    const t = api.get(slug)
    const fallback = slug.split(':')[1] ?? slug
    return { slug, label: tagLabel(slug, t?.label ?? fallback, pluralLabels), n: t?.count ?? 0, on: f.tags.includes(slug) }
  })
}

/**
 * Colour rows for one selected model row: facets already narrow `color` to
 * what exists under the current selection (model included), so this is just
 * that facet with slugs already shown under an earlier model removed —
 * colours are a shared tag, not per-model, so with several models selected
 * the same list would otherwise repeat under each of them.
 */
function modelColorRows(groups: TagGroup[] | null, f: Filters, used: Set<string>): Row[] {
  // Only this selection's colours: a colour with 0 under the chosen model belongs
  // to another model (e.g. MacBook «Silver» under iPhone Air) and is noise here.
  const rows = facet(groups, 'color', f).filter((r) => !used.has(r.slug) && (r.n > 0 || r.on))
  for (const r of rows) used.add(r.slug)
  return rows
}

/** Compact swatch row under a selected model: 22px ring, name, count. */
function ColorSwatches({ rows, onChange }: { rows: Row[]; onChange: (slug: string) => void }) {
  if (!rows.length) return null
  return (
    <div className="clrsw">
      {rows.map((r) => {
        const dim = !r.n && !r.on
        const hex = colorHex(r.label)
        return (
          <button
            key={r.slug}
            type="button"
            className={`swatch ${r.on ? 'on' : ''} ${dim ? 'dim' : ''}`}
            aria-pressed={r.on}
            aria-disabled={dim}
            title={r.label}
            onClick={dim ? undefined : () => onChange(r.slug)}
          >
            <i className="ring" style={{ background: hex ?? 'var(--fill-2)' }} />
            <span className="nm">{r.label}</span>
            <span className="c">{r.n}</span>
          </button>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Mobile: pill row + small dropdown menus
// ---------------------------------------------------------------------------

type PopKind = 'price' | 'grade'

export interface Chip {
  key: string
  label: string
  remove: () => void
}

/**
 * What the catalog palette picked (section, model, colour under the model),
 * as removable chips for the phone pill row — each once. Labels: the palette's
 * model names first («Samsung Galaxy S24»), then the facets.
 */
export function catalogChips(groups: TagGroup[] | null, f: Filters, onChange: (next: Filters) => void, names?: Map<string, string>): Chip[] {
  const labels = new Map<string, string>()
  for (const g of groups ?? []) for (const t of g.tags) labels.set(t.slug, tagLabel(t.slug, t.label, true))
  const labelOf = (s: string) => names?.get(s) ?? labels.get(s) ?? tagLabel(s, s.split(':')[1] ?? s, true)
  const without = (drop: (t: string) => boolean): Filters => ({ ...f, tags: f.tags.filter((t) => !drop(t)) })
  const isSection = (t: string) => t.startsWith('device:') || t.startsWith('brand:')

  const chips: Chip[] = []
  const cat = catOfTags(f, groups)
  if (cat) chips.push({ key: `cat:${cat.k}`, label: cat.name, remove: () => onChange(without(isSection)) })
  else for (const s of f.tags.filter(isSection)) chips.push({ key: s, label: labelOf(s), remove: () => onChange(toggleTag(f, s)) })

  const models = tagsOfKind(f, 'model')
  const colors = tagsOfKind(f, 'color')
  const merge = models.length === 1 && colors.length > 0
  for (const s of models) {
    const label = merge ? `${labelOf(s)} · ${colors.map(labelOf).join(', ')}` : labelOf(s)
    chips.push({ key: s, label, remove: () => onChange(toggleModelTag(f, s)) })
  }
  if (!merge) for (const s of colors) chips.push({ key: s, label: labelOf(s), remove: () => onChange(toggleTag(f, s)) })
  return chips
}

export function Pills({
  groups,
  f,
  onChange,
  onOpenFilters,
  loading,
  chips = [],
}: {
  groups: TagGroup[] | null
  f: Filters
  onChange: (next: Filters) => void
  onOpenFilters: () => void
  loading: boolean
  /** Palette picks, shown first as removable chips (not counted in the «Фильтры» badge). */
  chips?: Chip[]
}) {
  const [pop, setPop] = useState<PopKind | null>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const popRef = useRef<HTMLDivElement>(null)
  const n = filterCount(f) - (chips.length ? f.tags.filter((t) => CATALOG_KINDS.includes(t.split(':')[0])).length : 0)

  useEffect(() => {
    if (!pop) return
    const close = (e: PointerEvent) => {
      const t = e.target as Node
      if (popRef.current?.contains(t)) return
      if ((t as Element).closest?.('[data-pill]')) return
      setPop(null)
    }
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setPop(null)
    document.addEventListener('pointerdown', close)
    window.addEventListener('keydown', onKey)
    window.addEventListener('scroll', () => setPop(null), { once: true })
    return () => {
      document.removeEventListener('pointerdown', close)
      window.removeEventListener('keydown', onKey)
    }
  }, [pop])

  useLayoutEffect(() => {
    const p = popRef.current
    const wrap = wrapRef.current
    if (!p || !wrap || !pop) return
    const b = wrap.querySelector(`[data-pill="${pop}"]`)
    if (!b) return
    const x = b.getBoundingClientRect().left - wrap.getBoundingClientRect().left
    p.style.left = `${Math.min(Math.max(12, x), wrap.clientWidth - 12 - p.offsetWidth)}px`
    p.style.top = '56px'
  }, [pop])

  const pill = (k: PopKind, base: string) => {
    let lab = base
    let on = false
    if (k === 'price') {
      on = f.pmin != null || f.pmax != null
      if (on) lab = priceRangeLabel(f.pmin, f.pmax)
    } else {
      const set = tagsOfKind(f, k)
      on = set.length > 0
      if (set.length === 1) lab = facet(groups, k, f, true).find((r) => r.slug === set[0])?.label ?? base
      else if (set.length > 1) lab = `${base} · ${set.length}`
    }
    return (
      <button key={k} type="button" className={`pill press ${on ? 'on' : ''}`} data-pill={k} onClick={() => setPop(pop === k ? null : k)} aria-expanded={pop === k}>
        {lab}
        <ChevD />
      </button>
    )
  }

  return (
    <div className="pills-wrap" ref={wrapRef}>
      <div className="pills hs">
        {chips.map((c) => (
          <button key={c.key} type="button" className="pill press on" onClick={c.remove} aria-label={`Убрать фильтр ${c.label}`}>
            {c.label}
            <Xp />
          </button>
        ))}
        <button type="button" className={`pill press ${n ? 'on' : ''}`} onClick={onOpenFilters}>
          <Sliders />
          Фильтры
          {n > 0 && <span className="badge">{n}</span>}
        </button>
        {loading && !groups ? (
          <>
            <span className="pill sk" />
            <span className="pill sk" />
          </>
        ) : (
          <>
            {pill('price', 'Цена')}
            {pill('grade', 'Состояние')}
          </>
        )}
      </div>
      {pop && (
        <div className="pop" ref={popRef} role="menu">
          <PopMenu kind={pop} groups={groups} f={f} onChange={onChange} />
        </div>
      )}
    </div>
  )
}

function PopMenu({ kind, groups, f, onChange }: { kind: PopKind; groups: TagGroup[] | null; f: Filters; onChange: (next: Filters) => void }) {
  const item = (key: string, label: string, n: number | null, on: boolean, dim: boolean, act: () => void) => (
    <button key={key} type="button" role="menuitemcheckbox" aria-checked={on} className={`pop-i ${dim ? 'dim' : ''}`} onClick={dim ? undefined : act} aria-disabled={dim}>
      <span className="ck">{on ? <Check /> : null}</span>
      <span>{label}</span>
      <span className="c">{n ?? ''}</span>
    </button>
  )

  if (kind === 'price') {
    const band = activeBand(f)
    const none = f.pmin == null && f.pmax == null
    const rows = facet(groups, 'price', f)
    return (
      <>
        <div className="pop-k">Цена</div>
        {item('all', 'Любая цена', null, none, false, () => onChange({ ...f, pmin: null, pmax: null }))}
        <div className="pop-sep" />
        {PRICE_BANDS.map((b) => {
          const n = rows.find((r) => r.slug === `price:${b.code}`)?.n ?? 0
          const on = band === b.code
          // A band with no lot is outside the real price spread: hide it.
          if (!n && !on) return null
          return item(b.code, b.label, n, on, false, () => onChange(on ? { ...f, pmin: null, pmax: null } : { ...f, pmin: b.min, pmax: b.max }))
        })}
      </>
    )
  }

  const rows = facet(groups, kind, f, true)
  const none = !tagsOfKind(f, kind).length
  return (
    <>
      <div className="pop-k">Состояние</div>
      {item('all', 'Любое состояние', null, none, false, () => onChange(setKind(f, kind, [])))}
      <div className="pop-sep" />
      {rows.map((r) => item(r.slug, r.label, r.n, r.on, !r.n && !r.on, () => onChange(toggleTag(f, r.slug))))}
    </>
  )
}

// ---------------------------------------------------------------------------
// Price: histogram over a free two-handle slider
// ---------------------------------------------------------------------------

const STEP = 1000

export function PriceRange({ hist, pmin, pmax, onChange }: { hist: PriceHistogram | null; pmin: number | null; pmax: number | null; onChange: (pmin: number | null, pmax: number | null) => void }) {
  const rangeRef = useRef<HTMLDivElement>(null)
  const buckets = hist?.buckets ?? []
  const has = buckets.length > 0
  const lo = has ? buckets[0].from : 0
  const hi = has ? buckets[buckets.length - 1].to : 1
  const span = Math.max(1, hi - lo)
  const vlo = Math.min(Math.max(pmin ?? lo, lo), hi)
  const vhi = Math.max(Math.min(pmax ?? hi, hi), vlo)
  const x = (v: number) => ((v - lo) / span) * 100
  const max = Math.max(1, ...buckets.map((b) => b.count))

  const commit = (a: number, b: number) => onChange(a <= lo ? null : a, b >= hi ? null : b)

  const valueAt = (clientX: number) => {
    const r = rangeRef.current!.getBoundingClientRect()
    const frac = Math.max(0, Math.min(1, (clientX - r.left) / r.width))
    return Math.round((lo + frac * span) / STEP) * STEP
  }

  const drag = (which: 'lo' | 'hi') => (e: RPointerEvent<HTMLDivElement>) => {
    if (!has) return
    e.preventDefault()
    const el = e.currentTarget
    el.setPointerCapture(e.pointerId)
    const move = (ev: PointerEvent) => {
      const v = valueAt(ev.clientX)
      if (which === 'lo') commit(Math.min(v, vhiRef.current - STEP), vhiRef.current)
      else commit(vloRef.current, Math.max(v, vloRef.current + STEP))
    }
    const up = () => {
      el.removeEventListener('pointermove', move)
      el.removeEventListener('pointerup', up)
      el.removeEventListener('pointercancel', up)
    }
    el.addEventListener('pointermove', move)
    el.addEventListener('pointerup', up)
    el.addEventListener('pointercancel', up)
  }
  // Latest values for the drag closure (props change on every move).
  const vloRef = useRef(vlo)
  const vhiRef = useRef(vhi)
  vloRef.current = vlo
  vhiRef.current = vhi

  const key = (which: 'lo' | 'hi') => (e: RKeyboardEvent) => {
    const d = e.key === 'ArrowRight' || e.key === 'ArrowUp' ? STEP : e.key === 'ArrowLeft' || e.key === 'ArrowDown' ? -STEP : 0
    if (!d) return
    e.preventDefault()
    if (which === 'lo') commit(Math.max(lo, Math.min(vlo + d, vhi - STEP)), vhi)
    else commit(vlo, Math.min(hi, Math.max(vhi + d, vlo + STEP)))
  }

  // Only real values: the cheapest and the most expensive lot under the other
  // filters. Evenly spaced midpoints (70 000, 80 000…) matched no lot.
  const ticks = has ? [lo, hi] : []
  return (
    <>
      <div className="hist" aria-hidden="true">
        {has
          ? buckets.map((b, i) => (
              <i key={i} className={`${b.from < vhi && b.to > vlo ? 'in' : ''} ${b.count ? '' : 'z'}`} style={{ height: `${(b.count / max) * 100}%` }} />
            ))
          : Array.from({ length: 12 }).map((_, i) => <i key={i} className="z" />)}
      </div>
      <div className={`range ${has ? '' : 'off'}`} ref={rangeRef}>
        <div className="rail" />
        <div className="fillr" style={{ left: `${x(vlo)}%`, width: `${x(vhi) - x(vlo)}%` }} />
        <div className="knob" role="slider" tabIndex={0} aria-label="Минимальная цена" aria-valuemin={lo} aria-valuemax={hi} aria-valuenow={vlo} style={{ left: `${x(vlo)}%` }} onPointerDown={drag('lo')} onKeyDown={key('lo')} />
        <div className="knob" role="slider" tabIndex={0} aria-label="Максимальная цена" aria-valuemin={lo} aria-valuemax={hi} aria-valuenow={vhi} style={{ left: `${x(vhi)}%` }} onPointerDown={drag('hi')} onKeyDown={key('hi')} />
      </div>
      <div className="ticks" aria-hidden="true">
        {ticks.map((v, i) => (
          <span key={i} style={{ left: `${x(v)}%` }}>
            {formatPrice(v)}
          </span>
        ))}
      </div>
      <div className="minmax">
        <div className="fld">
          <small>Минимум</small>
          <b className={pmin != null && pmin > lo ? '' : 'ph'}>{has ? formatPrice(pmin != null && pmin > lo ? pmin : lo) : '—'}</b>
        </div>
        <span className="dash" />
        <div className="fld">
          <small>Максимум</small>
          <b className={pmax != null && pmax < hi ? '' : 'ph'}>{has ? formatPrice(pmax != null && pmax < hi ? pmax : hi) : '—'}</b>
        </div>
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// Filters sheet (phones): draft until «Показать N предложений»
// ---------------------------------------------------------------------------

export function FilterSheet({ applied, onApply, onClose }: { applied: Filters; onApply: (f: Filters) => void; onClose: () => void }) {
  const [draft, setDraft] = useState<Filters>(applied)
  const [groups, setGroups] = useState<TagGroup[] | null>(null)
  const [count, setCount] = useState<number | null>(null)
  const [hist, setHist] = useState<PriceHistogram | null>(null)
  const [more, setMore] = useState<Record<string, boolean>>({})
  const key = filtersKey(draft)
  const tagKey = draft.tags.join(',')

  // Live counter + faceted counts for the draft, debounced.
  useEffect(() => {
    setCount(null)
    const ac = new AbortController()
    const t = setTimeout(() => {
      const hasAny = filterCount(draft) > 0
      countItems(draft, ac.signal).then(setCount).catch(() => {})
      fetchTags(hasAny ? draft : undefined, ac.signal).then(setGroups).catch(() => {})
    }, 250)
    return () => {
      clearTimeout(t)
      ac.abort()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  // Histogram depends on the other filters only.
  useEffect(() => {
    const ac = new AbortController()
    const t = setTimeout(() => {
      fetchPriceHistogram(draft.tags, ac.signal).then(setHist).catch(() => {})
    }, 250)
    return () => {
      clearTimeout(t)
      ac.abort()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tagKey])

  const toggle = (slug: string) => setDraft((d) => toggleTag(d, slug))
  const n = filterCount(draft)

  const listSec = (prefix: string, title: string, limit?: number) => {
    const rows = facet(groups, prefix, draft, true)
    if (!rows.length) return null
    const shown = limit && !more[prefix] ? rows.slice(0, limit) : rows
    const model = prefix === 'model'
    const usedColors = new Set<string>()
    return (
      <section className="f-sec">
        <h4>{title}</h4>
        <div className="body">
          <div className="group">
            {shown.map((r) => {
              const dim = !r.n && !r.on
              const colorRows = model && r.on ? modelColorRows(groups, draft, usedColors) : []
              return (
                <Fragment key={r.slug}>
                  <button
                    type="button"
                    role="checkbox"
                    aria-checked={r.on}
                    className={`g-row ${dim ? 'dim' : ''}`}
                    onClick={dim ? undefined : () => setDraft((d) => (model ? toggleModelTag(d, r.slug) : toggleTag(d, r.slug)))}
                    aria-disabled={dim}
                  >
                    <span className="l">{r.label}</span>
                    <span className="c">{r.n}</span>
                    <span className="ck">{r.on ? <Check /> : null}</span>
                  </button>
                  <ColorSwatches rows={colorRows} onChange={toggle} />
                </Fragment>
              )
            })}
          </div>
          {limit && rows.length > limit && !more[prefix] && (
            <button type="button" className="more" onClick={() => setMore((m) => ({ ...m, [prefix]: true }))}>
              Ещё {rows.length - limit}
            </button>
          )}
        </div>
      </section>
    )
  }

  const chipSec = (prefix: string, title: string) => {
    const rows = facet(groups, prefix, draft)
    if (!rows.length) return null
    return (
      <section className="f-sec">
        <h4>{title}</h4>
        <div className="body">
          <div className="chips">
            {rows.map((r) => {
              const dim = !r.n && !r.on
              return (
                <button key={r.slug} type="button" aria-pressed={r.on} className={`chip press ${r.on ? 'on' : ''} ${dim ? 'dim' : ''}`} onClick={dim ? undefined : () => toggle(r.slug)} aria-disabled={dim}>
                  {r.label}
                  <span className="c">{r.n}</span>
                </button>
              )
            })}
          </div>
        </div>
      </section>
    )
  }

  // Состояние: segmented control (single choice), dim values with no lots.
  const gradeRows = facet(groups, 'grade', draft)
  const gradeSet = tagsOfKind(draft, 'grade')
  const cur = gradeSet.length === 1 ? gradeSet[0].split(':')[1].toUpperCase() : gradeSet.length ? 'multi' : ''

  let label = 'Показать предложения'
  if (count === 0) label = 'Ничего не найдено'
  else if (count != null) label = `Показать ${offers(count)}`

  return (
    <Sheet
      onClose={onClose}
      label="Фильтры"
      title="Фильтры"
      full
      footer={
        <>
          <button type="button" className="link-btn" disabled={!n} onClick={() => setDraft(NO_FILTERS)}>
            Сбросить
          </button>
          <button type="button" className="btn press num" disabled={count === 0} onClick={() => onApply(draft)}>
            {label}
          </button>
        </>
      }
    >
      <div className="sheet-body">
        <section className="f-sec">
          <h4>Цена</h4>
          <div className="body">
            <PriceRange hist={hist} pmin={draft.pmin} pmax={draft.pmax} onChange={(pmin, pmax) => setDraft((d) => ({ ...d, pmin, pmax }))} />
          </div>
        </section>
        <section className="f-sec">
          <h4>Состояние</h4>
          <div className="body">
            <div className="seg" role="radiogroup" aria-label="Состояние">
              <button type="button" role="radio" aria-checked={cur === ''} className={cur === '' ? 'on' : ''} onClick={() => setDraft((d) => setKind(d, 'grade', []))}>
                Любое
              </button>
              {GRADE_ORDER.map((g) => {
                const r = gradeRows.find((x) => x.slug === `grade:${g.toLowerCase()}`)
                const dim = !r?.n && cur !== g
                return (
                  <button
                    key={g}
                    type="button"
                    role="radio"
                    aria-checked={cur === g}
                    aria-disabled={dim}
                    className={`${cur === g ? 'on' : ''} ${dim ? 'dim' : ''}`}
                    onClick={dim ? undefined : () => setDraft((d) => setKind(d, 'grade', [`grade:${g.toLowerCase()}`]))}
                  >
                    {GRADE[g].seg}
                  </button>
                )
              })}
            </div>
          </div>
        </section>
        {listSec('device', 'Устройство')}
        {listSec('brand', 'Производитель')}
        {listSec('model', 'Модель', 5)}
        {chipSec('storage', 'Память')}
        {chipSec('battery', 'Батарея')}
        {chipSec('kit', 'Комплект')}
      </div>
    </Sheet>
  )
}

// ---------------------------------------------------------------------------
// Desktop: accordion sidebar + removable active-filter chips
// ---------------------------------------------------------------------------

const ACC: [string, string][] = [
  ['device', 'Устройство'],
  ['brand', 'Производитель'],
  ['model', 'Модель'],
  ['storage', 'Память'],
  ['grade', 'Состояние'],
  ['battery', 'Батарея'],
  ['kit', 'Комплект'],
  ['price', 'Цена'],
]

export function Accordions({ groups, f, onChange }: { groups: TagGroup[] | null; f: Filters; onChange: (next: Filters) => void }) {
  const [open, setOpen] = useState<Set<string>>(() => new Set(['device', 'grade', 'brand']))
  const band = activeBand(f)
  const usedModelColors = new Set<string>()
  return (
    <>
      {ACC.map(([k, title]) => {
        const isOpen = open.has(k)
        const rows = isOpen ? facet(groups, k, f, true) : []
        if (k !== 'price' && groups && !facet(groups, k, f).length) return null
        const model = k === 'model'
        return (
          <div key={k} className={`acc ${isOpen ? 'open' : ''}`}>
            <button
              type="button"
              className="acc-h"
              aria-expanded={isOpen}
              onClick={() =>
                setOpen((s) => {
                  const next = new Set(s)
                  if (next.has(k)) next.delete(k)
                  else next.add(k)
                  return next
                })
              }
            >
              {title}
              <ChevD14 />
            </button>
            {isOpen && (
              <div className="acc-b">
                {rows.map((r) => {
                  const on = k === 'price' ? band === r.slug.split(':')[1] : r.on
                  if (k === 'price' && !r.n && !on) return null
                  const dim = !r.n && !on
                  const act = () => {
                    if (k === 'price') {
                      const b = PRICE_BANDS.find((x) => `price:${x.code}` === r.slug)!
                      return onChange(on ? { ...f, pmin: null, pmax: null } : { ...f, pmin: b.min, pmax: b.max })
                    }
                    return onChange(model ? toggleModelTag(f, r.slug) : toggleTag(f, r.slug))
                  }
                  return (
                    <Fragment key={r.slug}>
                      <button type="button" className={`av ${on ? 'on' : ''} ${dim ? 'dim' : ''}`} onClick={dim ? undefined : act} aria-pressed={on} aria-disabled={dim}>
                        <span>{r.label}</span>
                        <span className="c">{on ? <CheckS /> : r.n}</span>
                      </button>
                      <ColorSwatches rows={model && on ? modelColorRows(groups, f, usedModelColors) : []} onChange={(slug) => onChange(toggleTag(f, slug))} />
                    </Fragment>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}
    </>
  )
}

export function AppliedChips({ groups, f, onChange }: { groups: TagGroup[] | null; f: Filters; onChange: (next: Filters) => void }) {
  const labels = useMemo(() => {
    const m = new Map<string, string>()
    for (const g of groups ?? []) for (const t of g.tags) m.set(t.slug, tagLabel(t.slug, t.label, true))
    return m
  }, [groups])
  const labelOf = (s: string) => labels.get(s) ?? tagLabel(s, s.split(':')[1] ?? s, true)

  // With exactly one model selected, fold its colours into one "Модель · Цвет"
  // chip instead of a separate colour chip (a colour means nothing on its own).
  const modelSlugs = tagsOfKind(f, 'model')
  const colorSlugs = tagsOfKind(f, 'color')
  const mergeModel = modelSlugs.length === 1 && colorSlugs.length ? modelSlugs[0] : null

  const chips: { key: string; label: string; remove: () => void }[] = []
  for (const s of f.tags) {
    if (mergeModel && s === mergeModel) {
      chips.push({ key: s, label: `${labelOf(s)} · ${colorSlugs.map(labelOf).join(', ')}`, remove: () => onChange(toggleModelTag(f, s)) })
      continue
    }
    if (mergeModel && colorSlugs.includes(s)) continue
    chips.push({ key: s, label: labelOf(s), remove: () => onChange(toggleTag(f, s)) })
  }

  return (
    <div className="ap">
      {chips.map((c) => (
        <button key={c.key} type="button" className="apc press" onClick={c.remove} aria-label={`Убрать фильтр ${c.label}`}>
          {c.label}
          <Xs />
        </button>
      ))}
      {(f.pmin != null || f.pmax != null) && (
        <button type="button" className="apc press" onClick={() => onChange({ ...f, pmin: null, pmax: null })} aria-label="Убрать фильтр цены">
          {priceRangeLabel(f.pmin, f.pmax)}
          <Xs />
        </button>
      )}
    </div>
  )
}

