import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ApiError, fetchItem, type ItemDetail } from '../api'
import LeadSheet from '../components/BuyButton'
import { DeskGallery, Gallery } from '../components/Gallery'
import { Back, CheckS, ChevR, Dash } from '../components/Icons'
import { useApp } from '../components/Nav'
import { cachedDetail, rememberDetail } from '../details'
import {
  DEVICE,
  GRADE,
  KIT,
  NO_FILTERS,
  SHOP_NAME,
  catOfItem,
  catTags,
  dateLong,
  dateRel,
  filtersQuery,
  formatPrice,
  goBack,
  headParts,
  storageLabel,
  useWide,
} from '../lib'
import { noteSeller, useMeta } from '../meta'
import { openSellerChat, useBackButton } from '../tg'

type State = { kind: 'loading' } | { kind: 'ok'; item: ItemDetail } | { kind: 'sold' } | { kind: 'notfound' } | { kind: 'error' }

export default function ItemPage() {
  const { publicId = '' } = useParams()
  const navigate = useNavigate()
  const { inTelegram } = useApp()
  const wide = useWide()
  const [state, setState] = useState<State>(() => {
    const hit = cachedDetail(publicId)
    return hit ? { kind: 'ok', item: hit } : { kind: 'loading' }
  })
  const [attempt, setAttempt] = useState(0)

  const back = useCallback(() => goBack(navigate), [navigate])
  useBackButton(back)

  useEffect(() => {
    const ac = new AbortController()
    const hit = cachedDetail(publicId)
    setState(hit ? { kind: 'ok', item: hit } : { kind: 'loading' })
    window.scrollTo(0, 0)
    fetchItem(publicId, ac.signal)
      .then((item) => {
        rememberDetail(item)
        noteSeller(item.seller_url)
        setState({ kind: 'ok', item })
      })
      .catch((e) => {
        if ((e as Error).name === 'AbortError') return
        if (e instanceof ApiError && e.status === 410) setState({ kind: 'sold' })
        else if (e instanceof ApiError && e.status === 404) setState({ kind: 'notfound' })
        else if (!hit) setState({ kind: 'error' })
      })
    return () => ac.abort()
  }, [publicId, attempt])

  useEffect(() => {
    document.title = state.kind === 'ok' ? `${state.item.title} — ${SHOP_NAME}` : SHOP_NAME
    return () => {
      document.title = SHOP_NAME
    }
  }, [state])

  const onSold = useCallback(() => setState({ kind: 'sold' }), [])
  const toList = () => navigate('/', { replace: true })

  let body: ReactNode
  if (state.kind === 'ok') body = wide ? <DeskItem item={state.item} onSold={onSold} /> : <MobileItem item={state.item} onSold={onSold} />
  else if (state.kind === 'loading') body = wide ? <DeskSkeleton /> : <MobileSkeleton />
  else {
    const q =
      state.kind === 'sold' ? (
        <>
          Уже продано. <span>Лот {publicId}.</span>
        </>
      ) : state.kind === 'notfound' ? (
        'Лот не найден.'
      ) : (
        'Не удалось загрузить.'
      )
    body = (
      <div className="state">
        <h1 className="q">{q}</h1>
        <div className="acts">
          {state.kind === 'error' && (
            <button type="button" className="btn press" onClick={() => setAttempt((a) => a + 1)}>
              Повторить
            </button>
          )}
          <button type="button" className={`btn press ${state.kind === 'error' ? 'tint' : ''}`} onClick={toList}>
            Все предложения
          </button>
        </div>
      </div>
    )
  }

  return (
    <>
      {wide ? <DeskLocalNav item={state.kind === 'ok' ? state.item : null} publicId={publicId} /> : <MobileTop publicId={publicId} showBack={!inTelegram} onBack={back} />}
      {body}
    </>
  )
}

// ---------------------------------------------------------------------------
// Shared bits
// ---------------------------------------------------------------------------

function KitList({ item }: { item: ItemDetail }) {
  const have = new Set(item.kit.map((k) => k.code))
  return (
    <div className="kitl">
      {KIT.map(([k, lab]) =>
        have.has(k) ? (
          <span key={k}>
            <CheckS />
            {lab}
          </span>
        ) : (
          <span key={k} className="no">
            <Dash />
            {lab}
          </span>
        ),
      )}
    </div>
  )
}

function Headline({ item }: { item: ItemDetail }) {
  const [a, b] = headParts(item)
  return (
    <h2 className="h2t">
      {a} {b && <span>{b}</span>}
    </h2>
  )
}

function useBuy(item: ItemDetail, onSold: () => void) {
  const [open, setOpen] = useState(false)
  const [sent, setSent] = useState(false)
  const { inTelegram } = useApp()
  const sheet = open ? (
    <LeadSheet
      item={item}
      inTelegram={inTelegram}
      sent={sent}
      onSent={() => setSent(true)}
      onClose={() => setOpen(false)}
      onSold={() => {
        setOpen(false)
        onSold()
      }}
    />
  ) : null
  return { open: () => setOpen(true), sheet, sent }
}

// ---------------------------------------------------------------------------
// Phones
// ---------------------------------------------------------------------------

function MobileTop({ publicId, showBack, onBack }: { publicId: string; showBack: boolean; onBack: () => void }) {
  return (
    <div className="m-top">
      <div className="m-nav">
        {showBack ? (
          <button type="button" className="back" onClick={onBack}>
            <Back />
            Каталог
          </button>
        ) : (
          <span />
        )}
        <span className="m-lotid">Лот {publicId}</span>
        <span style={{ width: 44 }} />
      </div>
    </div>
  )
}

function MobileItem({ item, onSold }: { item: ItemDetail; onSold: () => void }) {
  const buy = useBuy(item, onSold)
  const rows: [string, ReactNode][] = [['Модель', item.model || item.title]]
  if (item.device_type === 'laptop') rows.push(['Память', [item.variant, storageLabel(item.storage_gb) && `${storageLabel(item.storage_gb)} SSD`].filter(Boolean).join(' / ') || null])
  else if (item.storage_gb) rows.push(['Память', storageLabel(item.storage_gb)])
  else if (item.variant) rows.push(['Вариант', item.variant])
  rows.push(['Цвет', item.color])
  if (item.year) rows.push(['Год выпуска', String(item.year)])

  return (
    <>
      <Gallery media={item.media} title={item.title} device={item.device_type} color={item.color} />
      <div className="it-head">
        {item.published_at && <div className="kick">Опубликован {dateRel(item.published_at)}</div>}
        <Headline item={item} />
        <div className="price-hero">{formatPrice(item.price)}</div>
        {item.grade && (
          <div className="grade-line">
            <b>{GRADE[item.grade]?.short}</b>
          </div>
        )}
      </div>
      <h3 className="sec-t">Характеристики</h3>
      <div className="group inset">
        {rows.map(([k, v]) => (
          <div className="srow" key={k}>
            <span className="k">{k}</span>
            <span className={`v ${v ? '' : 'none'}`}>{v || '—'}</span>
          </div>
        ))}
        {item.battery_health != null && (
          <div className="srow col">
            <div className="top">
              <span className="k">Ёмкость аккумулятора</span>
              <span className="v">{item.battery_health}%</span>
            </div>
            <div className="bar">
              <i style={{ width: `${item.battery_health}%` }} />
            </div>
          </div>
        )}
        <div className="srow col">
          <div className="top">
            <span className="k">Комплект</span>
            <span className="v">{item.kit.length} из 4</span>
          </div>
          <KitList item={item} />
        </div>
      </div>
      {item.note && (
        <>
          <h3 className="sec-t">От продавца</h3>
          <div className="note">
            <p>{item.note}</p>
          </div>
        </>
      )}
      <div className="meta" aria-hidden="true" />
      <div className="abar">
        {item.seller_url && (
          <button type="button" className="btn tint press" onClick={() => openSellerChat(item.seller_url!)}>
            Написать
          </button>
        )}
        <button type="button" className="btn main press" onClick={buy.open}>
          {buy.sent ? 'Заявка отправлена' : 'Купить'}
        </button>
      </div>
      {buy.sheet}
    </>
  )
}

function MobileSkeleton() {
  return (
    <div aria-hidden="true">
      <div className="gal">
        <div className="sl">
          <div className="fr sk-block" style={{ borderRadius: 18 }} />
        </div>
      </div>
      <div className="it-head">
        <div className="sk-line" style={{ width: 120 }} />
        <div className="sk-block" style={{ height: 28, width: '80%', marginTop: 14 }} />
        <div className="sk-block" style={{ height: 34, width: 160, marginTop: 20 }} />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Desktop
// ---------------------------------------------------------------------------

function DeskLocalNav({ item, publicId }: { item: ItemDetail | null; publicId: string }) {
  const navigate = useNavigate()
  const { groups } = useMeta()
  const cat = item ? catOfItem(item) : null
  return (
    <div className="lnav">
      <div className="lnav-in">
        <div className="crumbs">
          <button type="button" onClick={() => navigate('/')}>
            Каталог
          </button>
          {cat && (
            <>
              <ChevR />
              <button type="button" onClick={() => navigate(`/${filtersQuery({ ...NO_FILTERS, tags: catTags(cat, groups) })}`)}>
                {cat.name}
              </button>
              <ChevR />
              <span>{item?.model || item?.title}</span>
            </>
          )}
        </div>
        <div className="r">
          <span className="num">Лот {publicId}</span>
        </div>
      </div>
    </div>
  )
}

function DeskItem({ item, onSold }: { item: ItemDetail; onSold: () => void }) {
  const buy = useBuy(item, onSold)
  const laptop = item.device_type === 'laptop'
  const st = storageLabel(item.storage_gb)
  const stats: [string, string][] = []
  if (laptop) {
    if (item.variant) stats.push([item.variant, 'Оперативная память'])
    stats.push([st ?? '—', 'Накопитель SSD'])
  } else if (st) stats.push([st, 'Память'])
  else stats.push([item.variant ?? '—', 'Вариант'])
  stats.push([item.battery_health != null ? `${item.battery_health}%` : '—', 'Аккумулятор'])

  const have = new Set(item.kit.map((k) => k.code))
  const dash = <span className="mute">—</span>
  // Rows whose value is the dash are dropped below: an iPhone has no RAM/year
  // field, and a column of dashes is noise.
  const allRows: [string, ReactNode][] = [
    ['Устройство', item.device_type ? (DEVICE[item.device_type] ?? item.device_type) : dash],
    ['Производитель', item.brand || dash],
    ['Модель', item.model || dash],
    ['Год выпуска', item.year ? String(item.year) : dash],
    ['Оперативная память', laptop && item.variant ? item.variant : dash],
    ['Накопитель', st ?? dash],
    ...(!laptop && item.variant ? ([['Вариант', item.variant]] as [string, ReactNode][]) : []),
    ['Цвет', item.color || dash],
    [
      'Ёмкость аккумулятора',
      item.battery_health != null ? (
        <>
          {item.battery_health}%
          <div className="bar">
            <i style={{ width: `${item.battery_health}%` }} />
          </div>
        </>
      ) : (
        dash
      ),
    ],
    [
      'Комплект',
      KIT.map(([k, lab], i) => (
        <span key={k}>
          {i > 0 && <span className="mute"> · </span>}
          <span className={have.has(k) ? '' : 'off'}>{lab}</span>
        </span>
      )),
    ],
  ]
  const rows = allRows.filter(([, v]) => v !== dash)

  return (
    <div className="d-wrap">
      <div className="d-item">
        <div>
          <DeskGallery media={item.media} title={item.title} device={item.device_type} color={item.color} />
          <div className="stats">
            {stats.map(([v, c]) => (
              <div className="stat" key={c}>
                <b>{v}</b>
                <span>{c}</span>
              </div>
            ))}
          </div>
          <h3 className="d-sect">Характеристики</h3>
          <div className="tbl">
            {rows.map(([k, v]) => (
              <div className="tr" key={k}>
                <span className="k">{k}</span>
                <span className="v">{v}</span>
              </div>
            ))}
          </div>
        </div>
        <aside className="sum">
          {item.published_at && <div className="kick">Опубликован {dateLong(item.published_at)}</div>}
          <Headline item={item} />
          <div className="price-hero">{formatPrice(item.price)}</div>
          {item.grade && (
            <div className="grade-line">
              <b>{GRADE[item.grade]?.short}</b>
            </div>
          )}
          <div className="acts">
            <button type="button" className="btn w100 press" onClick={buy.open}>
              {buy.sent ? 'Заявка отправлена' : 'Купить'}
            </button>
            {item.seller_url && (
              <button type="button" className="btn tint w100 press" onClick={() => openSellerChat(item.seller_url!)}>
                Написать продавцу
              </button>
            )}
          </div>
          <div className="card">
            <h5>Комплект</h5>
            <KitList item={item} />
          </div>
          {item.note && (
            <div className="card">
              <h5>От продавца</h5>
              <p>{item.note}</p>
            </div>
          )}
        </aside>
      </div>
      {buy.sheet}
    </div>
  )
}

function DeskSkeleton() {
  return (
    <div className="d-wrap" aria-hidden="true">
      <div className="d-item">
        <div className="sk-block" style={{ height: 440, borderRadius: 18 }} />
        <div>
          <div className="sk-line" style={{ width: 140 }} />
          <div className="sk-block" style={{ height: 38, width: '90%', marginTop: 14 }} />
          <div className="sk-block" style={{ height: 48, width: 200, marginTop: 20 }} />
          <div className="sk-block" style={{ height: 50, marginTop: 28, borderRadius: 999 }} />
        </div>
      </div>
    </div>
  )
}
