import { useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent as RKeyboardEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import type { MenuCategory, MenuModel, TagGroup } from '../api'
import { GRADE, GRADE_ORDER, NO_FILTERS, catByCode, catOfTags, catTags, filtersQuery, formatPrice, storageLabel, tagsOfKind, useScrollLock, type Category, type Filters } from '../lib'
import { useMeta } from '../meta'
import { useBackButton } from '../tg'
import { ProductImage } from './Art'
import { ChevR, Search, Xs } from './Icons'

/*
 * Catalog palette («C · Палитра», mockups/menu-variants/index.html): section
 * segments, a «Модель» field that narrows the models live, rows with a thumb,
 * «от X ₽» and a count, the condition split and «Все … · N». Desktop: popover
 * under the nav (⌘K) with a preview panel; phones: full-screen flow. Picks set
 * the same tags the filters use (`model:*`, `device:*` + `brand:*`, `grade:*`).
 */

const NB = ' '

// ---------------------------------------------------------------------------
// Data + formatting
// ---------------------------------------------------------------------------

interface Section {
  c: Category
  m: MenuCategory
}

function useSections(): { sections: Section[]; groups: TagGroup[] | null } {
  const { menu, groups } = useMeta()
  const sections = useMemo(() => (menu ?? []).filter((m) => m.count > 0).map((m) => ({ c: catByCode(m.code), m })), [menu])
  return { sections, groups }
}

/** Section to open on: the one the current filters point at, else the first. */
function startSection(sections: Section[], f: Filters, groups: TagGroup[] | null): string {
  const model = tagsOfKind(f, 'model')[0]
  const byModel = model ? sections.find((s) => s.m.models.some((m) => m.slug === model)) : null
  if (byModel) return byModel.m.code
  const cat = catOfTags(f, groups)
  const byCat = cat ? sections.find((s) => s.c.k === cat.k) : null
  return (byCat ?? sections[0])?.m.code ?? ''
}

function matchModels(m: MenuCategory | undefined, q: string): MenuModel[] {
  const t = q.trim().toLowerCase()
  return (m?.models ?? []).filter((x) => !t || x.label.toLowerCase().includes(t))
}

const grp = (n: number) => formatPrice(n).replace(`${NB}₽`, '')
const fromPrice = (m: MenuModel) => (m.price_min == null ? '' : `от${NB}${formatPrice(m.price_min)}`)

function priceSpan(lo: number | null, hi: number | null): string {
  if (lo == null || hi == null) return '—'
  return hi > lo ? `${grp(lo)}–${formatPrice(hi)}` : formatPrice(lo)
}

/** [128, 512] → «128–512 ГБ»; [512, 1024] → «512 ГБ – 1 ТБ»; [256] → «256 ГБ». */
function storageSpan(list: number[]): string {
  if (!list.length) return '—'
  const lo = Math.min(...list)
  const hi = Math.max(...list)
  if (lo === hi) return storageLabel(lo) ?? '—'
  const a = storageLabel(lo)!
  const b = storageLabel(hi)!
  const unit = (s: string) => s.split(NB)[1] ?? s.split(' ')[1]
  return unit(a) === unit(b) ? `${a.split(/[\s ]/)[0]}–${b}` : `${a} – ${b}`
}

/** [«45 мм», «49 мм»] → «45–49 мм»; mixed values → «a, b». */
function variantSpan(list: string[]): string {
  if (!list.length) return '—'
  const parsed = list.map((v) => /^(\d+(?:[.,]\d+)?)\s*(.+)$/.exec(v.trim()))
  if (parsed.every(Boolean) && new Set(parsed.map((p) => p![2])).size === 1) {
    const nums = parsed.map((p) => Number(p![1].replace(',', '.')))
    const lo = Math.min(...nums)
    const hi = Math.max(...nums)
    const unit = parsed[0]![2]
    return lo === hi ? `${lo}${NB}${unit}` : `${lo}–${hi}${NB}${unit}`
  }
  return list.join(', ')
}

function optionRow(c: Category, storages: number[], variants: string[]): [string, string] {
  if (storages.length) return ['Память', storageSpan(storages)]
  if (variants.length) return [c.k === 'watch' ? 'Корпус' : 'Вариант', variantSpan(variants)]
  return ['Память', '—']
}

function gradeLine(g: Record<string, number>): string {
  return GRADE_ORDER.filter((k) => g[k]).map((k) => `${GRADE[k].short} ${g[k]}`).join(' · ') || '—'
}

/** Model name with the typed part in bold (the rest greyed while filtering). */
function Hl({ name, q }: { name: string; q: string }) {
  const t = q.trim()
  const i = t ? name.toLowerCase().indexOf(t.toLowerCase()) : -1
  if (i < 0) return <span className="nm">{name}</span>
  return (
    <span className="nm hl">
      {name.slice(0, i)}
      <mark>{name.slice(i, i + t.length)}</mark>
      {name.slice(i + t.length)}
    </span>
  )
}

function Thumb({ m }: { m: MenuModel }) {
  return (
    <span className="th">
      <ProductImage src={m.cover} alt="" device={m.device_type} color={m.color} tight />
    </span>
  )
}

/** Picks → filters (the palette replaces the current filters, like a section link). */
function useGo(groups: TagGroup[] | null, onDone: () => void) {
  const navigate = useNavigate()
  return {
    go(f: Filters) {
      onDone()
      navigate(`/${filtersQuery(f)}`)
      window.scrollTo(0, 0)
    },
    model: (m: MenuModel): Filters => ({ ...NO_FILTERS, tags: [m.slug] }),
    all: (c: Category): Filters => ({ ...NO_FILTERS, tags: [...catTags(c, groups)].sort() }),
    grade: (c: Category, g: string): Filters => ({ ...NO_FILTERS, tags: [...catTags(c, groups), `grade:${g.toLowerCase()}`].sort() }),
  }
}

function Kc({ children }: { children: ReactNode }) {
  return <span className="kc">{children}</span>
}

// ---------------------------------------------------------------------------
// Desktop: popover under the nav
// ---------------------------------------------------------------------------

export function DeskPalette({ f, onClose }: { f: Filters; onClose: () => void }) {
  const { sections, groups } = useSections()
  const [code, setCode] = useState(() => startSection(sections, f, groups))
  const [q, setQ] = useState('')
  const [sel, setSel] = useState(0)
  const [flash, setFlash] = useState<number | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const nav = useGo(groups, onClose)

  const sec = sections.find((s) => s.m.code === code) ?? sections[0]
  useEffect(() => {
    if (!code && sections.length) setCode(startSection(sections, f, groups))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sections.length])

  const models = matchModels(sec?.m, q)
  const ALL = models.length // the «Все …» link is the last stop of ↑/↓
  const cur = Math.min(sel, ALL)

  // Open on the model the catalog is filtered by.
  useLayoutEffect(() => {
    const on = tagsOfKind(f, 'model')[0]
    const i = on ? models.findIndex((m) => m.slug === on) : -1
    if (i > 0) setSel(i)
    inputRef.current?.focus()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    listRef.current?.querySelector('.c-row.sel')?.scrollIntoView({ block: 'nearest' })
  }, [cur, code])

  const pick = (i: number) => {
    if (!sec) return
    const target = i < ALL ? nav.model(models[i]) : nav.all(sec.c)
    setFlash(i)
    window.setTimeout(() => nav.go(target), 160)
  }

  const onKey = (e: RKeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSel((cur + 1) % (ALL + 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSel((cur - 1 + ALL + 1) % (ALL + 1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      pick(cur)
    } else if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      if (q) {
        setQ('')
        setSel(0)
      } else onClose()
    }
  }

  const prev = sec ? (cur < ALL ? models[cur] : null) : null
  return (
    <>
      <div className="veil-c" onClick={onClose} />
      <div className="c-pop" role="dialog" aria-label="Каталог" onKeyDown={onKey}>
        <div className="c-seg" role="tablist">
          {sections.map((s) => (
            <button
              key={s.m.code}
              type="button"
              role="tab"
              aria-selected={s.m.code === sec?.m.code}
              className={s.m.code === sec?.m.code ? 'on' : ''}
              onClick={() => {
                setCode(s.m.code)
                setQ('')
                setSel(0)
                inputRef.current?.focus()
              }}
            >
              {s.c.name}
            </button>
          ))}
        </div>
        <label className="c-fil">
          <Search />
          <input
            ref={inputRef}
            value={q}
            placeholder="Модель"
            autoComplete="off"
            spellCheck={false}
            onChange={(e) => {
              setQ(e.target.value)
              setSel(0)
            }}
          />
          <Kc>esc</Kc>
        </label>
        <div className="c-body">
          <div className="c-list" ref={listRef}>
            <div className="c-k">Модели</div>
            {models.length ? (
              models.map((m, i) => (
                <button
                  key={m.slug}
                  type="button"
                  className={`c-row ${i === cur ? 'sel' : ''} ${flash === i ? 'go' : ''}`}
                  onMouseMove={() => i !== cur && setSel(i)}
                  onClick={() => pick(i)}
                >
                  <Thumb m={m} />
                  <Hl name={m.label} q={q} />
                  <span className="p num">{fromPrice(m)}</span>
                  <span className="c num">{m.count}</span>
                </button>
              ))
            ) : (
              <div className="c-empty">Ничего не нашлось.</div>
            )}
            <div className="c-k">Состояние</div>
            {sec &&
              GRADE_ORDER.map((g) => {
                const n = sec.m.grades[g] ?? 0
                return (
                  <button key={g} type="button" className={`c-row g ${n ? '' : 'dim'}`} onClick={n ? () => nav.go(nav.grade(sec.c, g)) : undefined} aria-disabled={!n}>
                    <span className="lt-g">
                      <span>{g}</span>
                    </span>
                    <span className="nm">{GRADE[g].short}</span>
                    <span />
                    <span className="c num">{n}</span>
                  </button>
                )
              })}
          </div>
          <div className="c-prev">{sec && <Preview s={sec} m={prev} />}</div>
        </div>
        <div className="c-foot">
          {sec ? (
            <button type="button" className={`c-all ${cur === ALL ? 'sel' : ''} ${flash === ALL ? 'go' : ''}`} onMouseMove={() => cur !== ALL && setSel(ALL)} onClick={() => pick(ALL)}>
              {sec.c.all} · {sec.m.count}
            </button>
          ) : (
            <span />
          )}
          <div className="c-hint">
            <span>
              <Kc>↑</Kc>
              <Kc>↓</Kc>выбор
            </span>
            <span>
              <Kc>↵</Kc>открыть
            </span>
            <span>
              <Kc>esc</Kc>закрыть
            </span>
          </div>
        </div>
      </div>
    </>
  )
}

/** Right panel: the highlighted model's numbers (or the whole section on «Все …»). */
function Preview({ s, m }: { s: Section; m: MenuModel | null }) {
  const models = s.m.models
  const lead = m ?? models[0]
  const title = m ? m.label : s.c.all
  const count = m ? m.count : s.m.count
  const prices = m ? [m.price_min, m.price_max] : [Math.min(...models.map((x) => x.price_min ?? Infinity)), Math.max(...models.map((x) => x.price_max ?? -Infinity))]
  const lo = Number.isFinite(prices[0]) ? prices[0] : null
  const hi = Number.isFinite(prices[1]) ? prices[1] : null
  const storages = m ? m.storages : [...new Set(models.flatMap((x) => x.storages))]
  const variants = m ? m.variants : [...new Set(models.flatMap((x) => x.variants))]
  const [optK, optV] = optionRow(s.c, storages, variants)
  return (
    <>
      <div className="im">{lead && <ProductImage key={lead.slug} src={lead.cover} alt="" device={lead.device_type} color={lead.color} eager />}</div>
      <h4>{title}</h4>
      <div className="c-kv">
        <span>Предложений</span>
        <b className="num">{count}</b>
      </div>
      <div className="c-kv">
        <span>Цена</span>
        <b className="num">{priceSpan(lo, hi)}</b>
      </div>
      <div className="c-kv">
        <span>{optK}</span>
        <b>{optV}</b>
      </div>
      <div className="c-kv">
        <span>Состояние</span>
        <b>{gradeLine(m ? m.grades : s.m.grades)}</b>
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// Phones: full-screen palette
// ---------------------------------------------------------------------------

/** Visible viewport (shrinks when the on-screen keyboard opens). */
function useVisualViewport(): { top: number; height: number } | null {
  const [vv, setVv] = useState<{ top: number; height: number } | null>(null)
  useEffect(() => {
    const v = window.visualViewport
    if (!v) return
    const on = () => setVv({ top: v.offsetTop, height: v.height })
    on()
    v.addEventListener('resize', on)
    v.addEventListener('scroll', on)
    return () => {
      v.removeEventListener('resize', on)
      v.removeEventListener('scroll', on)
    }
  }, [])
  return vv
}

export function MobilePalette({ f, onClose }: { f: Filters; onClose: () => void }) {
  const { sections, groups } = useSections()
  const [code, setCode] = useState(() => startSection(sections, f, groups))
  const [q, setQ] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const nav = useGo(groups, onClose)
  const vv = useVisualViewport()
  useScrollLock()
  useBackButton(onClose)

  useEffect(() => {
    if (!code && sections.length) setCode(startSection(sections, f, groups))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sections.length])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const sec = sections.find((s) => s.m.code === code) ?? sections[0]
  const models = matchModels(sec?.m, q)

  return createPortal(
    <div className="cp" role="dialog" aria-modal="true" aria-label="Каталог" style={vv ? { top: vv.top, height: vv.height, bottom: 'auto' } : undefined}>
      <div className="cp-top">
        <label className="cm-fil">
          <Search />
          <input
            ref={inputRef}
            value={q}
            placeholder="Модель"
            autoComplete="off"
            spellCheck={false}
            enterKeyHint="search"
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && inputRef.current?.blur()}
          />
          {q && (
            <button
              type="button"
              className="cm-clr"
              aria-label="Очистить"
              onClick={() => {
                setQ('')
                inputRef.current?.focus()
              }}
            >
              <Xs />
            </button>
          )}
        </label>
        <button type="button" className="cp-cancel" onClick={onClose}>
          Отмена
        </button>
      </div>
      <div className="cm-seg" role="tablist">
        {sections.map((s) => (
          <button
            key={s.m.code}
            type="button"
            role="tab"
            aria-selected={s.m.code === sec?.m.code}
            className={s.m.code === sec?.m.code ? 'on' : ''}
            onClick={() => setCode(s.m.code)}
          >
            {s.c.short}
          </button>
        ))}
      </div>
      <div className="cp-scroll">
        <div className="cm-list">
          <div className="cm-k">Модели</div>
          {models.length ? (
            models.map((m) => (
              <button key={m.slug} type="button" className="cm-row" onClick={() => nav.go(nav.model(m))}>
                <Thumb m={m} />
                <Hl name={m.label} q={q} />
                <span className="p num">{fromPrice(m)}</span>
                <span className="c num">{m.count}</span>
              </button>
            ))
          ) : (
            <div className="c-empty">Ничего не нашлось.</div>
          )}
          <div className="cm-k">Состояние</div>
          {sec &&
            GRADE_ORDER.map((g) => {
              const n = sec.m.grades[g] ?? 0
              return (
                <button key={g} type="button" className={`cm-row g ${n ? '' : 'dim'}`} onClick={n ? () => nav.go(nav.grade(sec.c, g)) : undefined} aria-disabled={!n}>
                  <span className="lt-g">
                    <span>{g}</span>
                  </span>
                  <span className="nm">{GRADE[g].short}</span>
                  <span className="c num">{n}</span>
                </button>
              )
            })}
        </div>
        {sec && !q.trim() && (
          <button type="button" className="cm-all" onClick={() => nav.go(nav.all(sec.c))}>
            <span>
              {sec.c.all} · {sec.m.count}
            </span>
            <ChevR />
          </button>
        )}
      </div>
    </div>,
    document.body,
  )
}
