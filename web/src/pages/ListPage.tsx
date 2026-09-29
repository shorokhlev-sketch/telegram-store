import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ApiError, fetchItems, fetchPriceHistogram, fetchTags, type ItemCard as Card, type TagGroup } from '../api'
import { Link } from 'react-router-dom'
import { Accordions, AppliedChips, FilterSheet, Pills, catalogChips } from '../components/Filters'
import { Search17, Sliders } from '../components/Icons'
import { Cell, CellSkeleton, Tile, TileSkeleton } from '../components/ItemCard'
import { useApp } from '../components/Nav'
import { MobilePalette } from '../components/Palette'
import { NO_FILTERS, SHOP_NAME, catOf, filterCount, filtersKey, filtersQuery, formatCount, formatPrice, offers, parseFilters, useWide, type Filters } from '../lib'
import { noteSeller, useMeta } from '../meta'

// Survives ItemPage round-trips (the list unmounts), so Back restores the
// loaded pages and the scroll position instead of refetching from the top.
// `loaded` = at least one page actually arrived for this key. Without it an
// aborted first request (the page mounts twice in Telegram when the admin
// check re-renders the header) left an empty 'cached' list and the catalog
// stayed blank until a filter changed the key.
let listCache: { key: string; loaded: boolean; items: Card[]; cursor: string | null; done: boolean; scrollY: number; total: number | null } | null = null
const facetCache = new Map<string, TagGroup[]>()

type Status = 'idle' | 'loading' | 'error'

export default function ListPage() {
  const [params, setParams] = useSearchParams()
  const f = useMemo(() => parseFilters(params), [params])
  const key = filtersKey(f)
  const wide = useWide()
  const meta = useMeta()

  const cached = listCache?.key === key && listCache.loaded ? listCache : null
  const [items, setItems] = useState<Card[]>(cached?.items ?? [])
  const [cursor, setCursor] = useState<string | null>(cached?.cursor ?? null)
  const [done, setDone] = useState(cached?.done ?? false)
  const [status, setStatus] = useState<Status>(cached ? 'idle' : 'loading')
  const [total, setTotal] = useState<number | null>(cached?.total ?? null)
  const [facets, setFacets] = useState<TagGroup[] | null>(facetCache.get(key) ?? null)
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)

  const reqRef = useRef<AbortController | null>(null)
  const sentinelRef = useRef<HTMLDivElement>(null)

  // --- facets (counts for pills, sheet, sidebar) ---------------------------
  useEffect(() => {
    const ac = new AbortController()
    fetchTags(filterCount(f) ? f : undefined, ac.signal)
      .then((g) => {
        facetCache.set(key, g)
        setFacets(g)
      })
      .catch(() => {})
    return () => ac.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  // --- items ----------------------------------------------------------------
  const loadPage = useCallback(
    (from: string | null, reset: boolean) => {
      reqRef.current?.abort()
      const ac = new AbortController()
      reqRef.current = ac
      setStatus('loading')
      fetchItems(f, from, ac.signal)
        .then((page) => {
          if (ac.signal.aborted) return
          setItems((prev) => (reset ? page.items : [...prev, ...page.items.filter((n) => !prev.some((p) => p.public_id === n.public_id))]))
          setCursor(page.next_cursor)
          setDone(!page.next_cursor)
          setTotal(page.total)
          setStatus('idle')
          noteSeller(page.items[0]?.seller_url)
        })
        .catch((e) => {
          if ((e as Error).name === 'AbortError') return
          setStatus('error')
          if (e instanceof ApiError && e.status === 400) setDone(true)
        })
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key],
  )

  const firstRun = useRef(true)
  useEffect(() => {
    if (firstRun.current) {
      firstRun.current = false
      if (cached) return
    }
    setItems([])
    setCursor(null)
    setDone(false)
    setTotal(null)
    loadPage(null, true)
    return () => reqRef.current?.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  useEffect(() => {
    const loaded = total != null
    listCache = { key, loaded, items, cursor, done, total, scrollY: listCache?.key === key ? listCache.scrollY : 0 }
  }, [key, items, cursor, done, total])

  useLayoutEffect(() => {
    if (cached && cached.scrollY > 0) window.scrollTo(0, cached.scrollY)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Scroll: remember position; hairline under the sticky pill bar once scrolled.
  const [scrolled, setScrolled] = useState(false)
  useEffect(() => {
    const onScroll = () => {
      if (listCache && listCache.key === key && !document.documentElement.classList.contains('pushed')) listCache.scrollY = window.scrollY
      setScrolled(window.scrollY > 4)
    }
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [key])

  // Infinite scroll (cursor paging).
  useEffect(() => {
    const el = sentinelRef.current
    if (!el || done || status !== 'idle' || !cursor) return
    const io = new IntersectionObserver((entries) => entries.some((e) => e.isIntersecting) && loadPage(cursor, false), { rootMargin: '600px 0px' })
    io.observe(el)
    return () => io.disconnect()
  }, [cursor, done, status, loadPage])

  const apply = useCallback(
    (next: Filters) => {
      setParams(new URLSearchParams(filtersQuery(next)), { replace: true })
      window.scrollTo({ top: 0 })
    },
    [setParams],
  )

  // Desktop title: category, single model, else «Вся техника». Phones: «Предложения N» / «N из M».
  const cat = catOf(f, meta.groups)
  const models = f.tags.filter((t) => t.startsWith('model:'))
  const modelNames = useMemo(() => new Map((meta.menu ?? []).flatMap((c) => c.models.map((m) => [m.slug, m.label] as [string, string]))), [meta.menu])
  const modelLabel = models.length === 1 ? (modelNames.get(models[0]) ?? facets?.find((g) => g.kind === 'model')?.tags.find((t) => t.slug === models[0])?.label) : null
  const title = cat ? cat.name : (modelLabel ?? 'Каталог')
  const n = filterCount(f)
  const count = total == null ? '' : n && meta.total != null ? `${formatCount(total)} из ${formatCount(meta.total)}` : formatCount(total)

  useEffect(() => {
    document.title = n ? `${title} — ${SHOP_NAME}` : SHOP_NAME
  }, [title, n])

  const initialLoading = status === 'loading' && items.length === 0
  const empty = status === 'idle' && items.length === 0
  const shared = { f, apply, facets, items, status, initialLoading, empty, sentinelRef, loadPage, cursor }

  if (wide) return <DeskCatalog {...shared} title={cat ? cat.name : modelLabel ?? 'Вся техника'} count={total} allCount={meta.total} />

  return (
    <>
      <PushedPage active={filtersOpen}>
        <MobileTop />
        <h1 className="pt">
          Предложения <span className="num">{count}</span>
        </h1>
        <button type="button" className="c-trig" onClick={() => setPaletteOpen(true)}>
          <Search17 />
          <span>Каталог</span>
        </button>
        <div className={`m-top pills-bar cat ${scrolled ? 'scrolled' : ''}`}>
          <Pills
            groups={facets}
            f={f}
            onChange={apply}
            onOpenFilters={() => setFiltersOpen(true)}
            loading={!facets}
            chips={catalogChips(facets ?? meta.groups, f, apply, modelNames)}
          />
        </div>
        <div className="grid2">
          {initialLoading && Array.from({ length: 6 }).map((_, i) => <TileSkeleton key={i} />)}
          {items.map((it) => (
            <Tile key={it.public_id} item={it} />
          ))}
          {status === 'loading' && items.length > 0 && Array.from({ length: 2 }).map((_, i) => <TileSkeleton key={`m${i}`} />)}
          {empty && <EmptyState filtered={n > 0} onReset={() => apply(NO_FILTERS)} />}
          {status === 'error' && <ErrorState retry={() => loadPage(items.length ? cursor : null, items.length === 0)} />}
          <div ref={sentinelRef} style={{ gridColumn: '1/-1', height: 1 }} aria-hidden="true" />
        </div>
      </PushedPage>
      {paletteOpen && <MobilePalette f={f} onClose={() => setPaletteOpen(false)} />}
      {filtersOpen && (
        <FilterSheet
          applied={f}
          onClose={() => setFiltersOpen(false)}
          onApply={(next) => {
            setFiltersOpen(false)
            apply(next)
          }}
        />
      )}
    </>
  )
}

/** Shop name (+ «Админка» for the seller). */
function MobileTop() {
  const { isAdmin } = useApp()
  return (
    <div className="m-top">
      <div className="m-nav cat">
        <span className="m-brand">{SHOP_NAME}</span>
        {isAdmin && (
          <Link to="/admin" className="m-admin">
            Админка
          </Link>
        )}
      </div>
    </div>
  )
}

/** While the filters sheet is open the catalog steps back (scaled, dimmed, rounded). */
function PushedPage({ active, children }: { active: boolean; children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const root = document.documentElement
    const el = ref.current
    if (!el) return
    if (active) {
      el.style.transformOrigin = `50% ${window.scrollY}px`
      root.classList.add('pushed')
    } else {
      root.classList.remove('pushed')
    }
    return () => root.classList.remove('pushed')
  }, [active])
  return (
    <div ref={ref} className={`page ${active ? 'pushed' : ''}`}>
      {children}
    </div>
  )
}

function EmptyState({ filtered, onReset }: { filtered: boolean; onReset: () => void }) {
  return (
    <div className="empty">
      <p>{filtered ? 'Ничего не нашлось.' : 'Пока пусто.'}</p>
      {filtered && (
        <button type="button" className="more" onClick={onReset}>
          Сбросить фильтры
        </button>
      )}
    </div>
  )
}

function ErrorState({ retry }: { retry: () => void }) {
  return (
    <div className="empty">
      <p>Не удалось загрузить.</p>
      <button type="button" className="more" onClick={retry}>
        Повторить
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Desktop catalog
// ---------------------------------------------------------------------------

interface DeskProps {
  f: Filters
  apply: (f: Filters) => void
  facets: TagGroup[] | null
  items: Card[]
  status: Status
  initialLoading: boolean
  empty: boolean
  sentinelRef: React.RefObject<HTMLDivElement>
  loadPage: (from: string | null, reset: boolean) => void
  cursor: string | null
  title: string
  count: number | null
  allCount: number | null
}

function DeskCatalog({ f, apply, facets, items, status, initialLoading, empty, sentinelRef, loadPage, cursor, title, count, allCount }: DeskProps) {
  const [sidebar, setSidebar] = useState(true)
  const n = filterCount(f)
  // Hero: the section name + its price span (data, from the histogram).
  const [span, setSpan] = useState<[number, number] | null>(null)
  const tagKey = f.tags.join(',')
  useEffect(() => {
    const ac = new AbortController()
    fetchPriceHistogram(f.tags, ac.signal)
      .then((h) => setSpan(h.min != null && h.max != null ? [h.min, h.max] : null))
      .catch(() => setSpan(null))
    return () => ac.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tagKey])

  const countText = count == null ? '' : n && allCount != null ? `${formatCount(count)} из ${formatCount(allCount)}` : offers(count)
  return (
    <>
      <div className="lnav">
        <div className="lnav-in">
          <h2>{title}</h2>
          <div className="r">
            <span className="num">{countText}</span>
          </div>
        </div>
      </div>
      <div className="d-wrap">
        <div className="d-hero">
          <h1>
            {title}.{' '}
            {span && (
              <span className="num">
                {span[0] === span[1] ? `${formatPrice(span[0])}.` : `От ${formatPrice(span[0]).replace(' ₽', '')} до ${formatPrice(span[1])}.`}
              </span>
            )}
          </h1>
        </div>
        <div className="fbar">
          <div className="l">
            <button type="button" className="tgl" onClick={() => setSidebar((s) => !s)}>
              <Sliders />
              {sidebar ? 'Скрыть фильтры' : 'Показать фильтры'}
            </button>
            <AppliedChips groups={facets} f={f} onChange={apply} />
          </div>
          <span className="sort">
            Сортировка: <b>сначала новые</b>
          </span>
        </div>
        <div className={`d-cat ${sidebar ? '' : 'nosb'}`}>
          <aside className="sb">{sidebar && <Accordions groups={facets} f={f} onChange={apply} />}</aside>
          <div className="dgrid">
            {initialLoading && Array.from({ length: 6 }).map((_, i) => <CellSkeleton key={i} />)}
            {items.map((it) => (
              <Cell key={it.public_id} item={it} />
            ))}
            {status === 'loading' && items.length > 0 && Array.from({ length: 3 }).map((_, i) => <CellSkeleton key={`m${i}`} />)}
            {empty && (
              <div className="d-empty">
                <p>{n ? 'Ничего не нашлось.' : 'Пока пусто.'}</p>
                {n > 0 && (
                  <button type="button" className="more" onClick={() => apply(NO_FILTERS)}>
                    Сбросить фильтры
                  </button>
                )}
              </div>
            )}
            {status === 'error' && (
              <div className="d-empty">
                <p>Не удалось загрузить.</p>
                <button type="button" className="more" onClick={() => loadPage(items.length ? cursor : null, items.length === 0)}>
                  Повторить
                </button>
              </div>
            )}
            <div ref={sentinelRef} style={{ gridColumn: '1/-1', height: 1 }} aria-hidden="true" />
          </div>
        </div>
      </div>
    </>
  )
}
