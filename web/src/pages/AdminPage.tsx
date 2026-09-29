import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { ApiError, fetchAdminItems, fetchAdminMe, postAdminSold, type AdminItem, type AdminPatchResult } from '../api'
import LotEditSheet from '../components/admin/LotEditSheet'
import '../components/admin/admin-edit.css'
import { ProductImage } from '../components/Art'
import { Back } from '../components/Icons'
import { useApp } from '../components/Nav'
import { Dialog } from '../components/Sheet'
import { formatPrice, goBack, plural, useWide } from '../lib'
import { hasInitData, haptic, initData, inTelegramUI, useBackButton } from '../tg'

type Tab = 'active' | 'sold'
type Auth = 'checking' | 'denied' | 'ok'

export default function AdminPage() {
  const navigate = useNavigate()
  const back = useCallback(() => goBack(navigate), [navigate])
  useBackButton(back, inTelegramUI())
  const [auth, setAuth] = useState<Auth>('checking')

  useEffect(() => {
    // No initData (site, or Mini App opened from the reply-keyboard button)
    // never even calls the API: it can never pass anyway, and we don't want
    // to hit an admin-only endpoint from the open web.
    if (!hasInitData()) {
      setAuth('denied')
      return
    }
    const ac = new AbortController()
    fetchAdminMe(initData(), ac.signal)
      .then(() => setAuth('ok'))
      .catch((e) => {
        if ((e as Error).name !== 'AbortError') setAuth('denied')
      })
    return () => ac.abort()
  }, [])

  return (
    <>
      <AdminTop />
      {auth === 'checking' && <div className="state" aria-busy="true" />}
      {auth === 'denied' && (
        <div className="state">
          <h1 className="q">Доступ только для продавца.</h1>
          <div className="acts">
            <button type="button" className="btn press" onClick={() => navigate('/', { replace: true })}>
              Все предложения
            </button>
          </div>
        </div>
      )}
      {auth === 'ok' && <AdminBoard />}
    </>
  )
}

function AdminTop() {
  const navigate = useNavigate()
  const { inTelegram } = useApp()
  const wide = useWide()
  if (wide)
    return (
      <div className="lnav">
        <div className="lnav-in">
          <h2>Админка</h2>
        </div>
      </div>
    )
  return (
    <div className="m-top">
      <div className="m-nav">
        {!inTelegram ? (
          <button type="button" className="back" onClick={() => goBack(navigate)}>
            <Back />
            Каталог
          </button>
        ) : (
          <span />
        )}
        <span className="m-lotid">Админка</span>
        <span style={{ width: 44 }} />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Board: segmented tabs + inset grouped list + sheets
// ---------------------------------------------------------------------------

type Toast = { text: string; warn?: boolean }
type Status = 'loading' | 'idle' | 'error'

function AdminBoard() {
  const [tab, setTab] = useState<Tab>('active')
  const [items, setItems] = useState<AdminItem[]>([])
  const [status, setStatus] = useState<Status>('loading')
  const [toast, setToast] = useState<Toast | null>(null)
  const [soldTarget, setSoldTarget] = useState<AdminItem | null>(null)
  const [editTarget, setEditTarget] = useState<AdminItem | null>(null)

  const load = useCallback((t: Tab, signal?: AbortSignal) => {
    setStatus('loading')
    fetchAdminItems(t, initData(), signal)
      .then((rows) => {
        setItems(rows)
        setStatus('idle')
      })
      .catch((e) => {
        if ((e as Error).name === 'AbortError') return
        setStatus('error')
      })
  }, [])

  useEffect(() => {
    const ac = new AbortController()
    load(tab, ac.signal)
    return () => ac.abort()
  }, [tab, load])

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 3200)
    return () => clearTimeout(t)
  }, [toast])

  const confirmSold = async (target: AdminItem) => {
    try {
      const res = await postAdminSold(target.public_id, initData())
      haptic('success')
      setItems((prev) => prev.filter((it) => it.public_id !== target.public_id))
      setToast(res.failed_message_ids.length ? { text: 'Пост в канале не удалился — помечен как ПРОДАНО', warn: true } : { text: 'Лот отмечен как проданный' })
    } catch (e) {
      haptic('error')
      setToast({ text: soldErrorText(e), warn: true })
    } finally {
      setSoldTarget(null)
    }
  }

  const replaceRow = (updated: AdminPatchResult) => {
    const { channel_updated: _, ...row } = updated
    setItems((prev) => prev.map((it) => (it.public_id === row.public_id ? row : it)))
  }

  const onEditSaved = (updated: AdminPatchResult) => {
    replaceRow(updated)
    haptic('success')
    setToast(updated.channel_updated ? { text: 'Сохранено' } : { text: 'Пост в канале не обновился', warn: true })
    setEditTarget(null)
  }

  const onCoverChanged = (updated: AdminPatchResult) => {
    replaceRow(updated)
    haptic('success')
    setToast(updated.channel_updated ? { text: 'Обложка изменена' } : { text: 'Пост в канале не обновился', warn: true })
  }

  const count = status === 'idle' ? `${items.length} ${plural(items.length, ['лот', 'лота', 'лотов'])}` : ''

  return (
    <div className="adm">
      <div className="ltitle">
        <h1>Админка</h1>
        <p className="num">{count}</p>
      </div>
      <div className="adm-seg">
        <div className="seg" role="tablist">
          <SegButton on={tab === 'active'} onClick={() => setTab('active')}>
            Активные
          </SegButton>
          <SegButton on={tab === 'sold'} onClick={() => setTab('sold')}>
            Проданные
          </SegButton>
        </div>
      </div>

      {status === 'loading' && (
        <div className="group inset" aria-hidden="true">
          {Array.from({ length: 3 }).map((_, i) => (
            <div className="adm-row" key={i}>
              <div className="th sk-block" />
              <div className="tx">
                <div className="sk-line" style={{ width: '70%', marginTop: 0 }} />
                <div className="sk-line" style={{ width: '40%' }} />
              </div>
            </div>
          ))}
        </div>
      )}
      {status === 'error' && (
        <div className="empty">
          <p>Не удалось загрузить.</p>
          <button type="button" className="more" onClick={() => load(tab)}>
            Повторить
          </button>
        </div>
      )}
      {status === 'idle' && items.length === 0 && (
        <div className="empty">
          <p>{tab === 'active' ? 'Активных лотов нет.' : 'Проданных лотов нет.'}</p>
        </div>
      )}
      {status === 'idle' && items.length > 0 && (
        <div className="group inset">
          {items.map((it) => (
            <div className="adm-row stack" key={it.public_id}>
              <div className="th">
                <ProductImage src={it.cover} alt="" device={it.device_type} color={it.color} />
              </div>
              <div className="tx">
                <b>{it.title}</b>
                <span>
                  {formatPrice(it.price)} · {it.lead_count} {plural(it.lead_count, ['заявка', 'заявки', 'заявок'])}
                </span>
                {it.status === 'active' && (
                  <div className="ac">
                    <button type="button" className="btn tint press" onClick={() => setEditTarget(it)}>
                      Изменить
                    </button>
                    <button type="button" className="btn tint press" onClick={() => setSoldTarget(it)}>
                      Продано
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {soldTarget && (
        <Dialog onClose={() => setSoldTarget(null)} label="Отметить продажу">
          <div className="buy">
            <SoldConfirm item={soldTarget} onConfirm={() => confirmSold(soldTarget)} />
          </div>
        </Dialog>
      )}
      {editTarget && <LotEditSheet item={editTarget} onClose={() => setEditTarget(null)} onSaved={onEditSaved} onCover={onCoverChanged} />}

      {toast && (
        <div className={`toast ${toast.warn ? 'warn' : ''}`} role="status">
          {toast.text}
        </div>
      )}
    </div>
  )
}

function SegButton({ on, onClick, children }: { on: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" role="tab" aria-selected={on} className={on ? 'on' : ''} onClick={onClick}>
      {children}
    </button>
  )
}

function LotLine({ item }: { item: AdminItem }) {
  return (
    <div className="lotline">
      <div className="th">
        <ProductImage src={item.cover} alt="" device={null} eager />
      </div>
      <div>
        <b>{item.title}</b>
        <span>
          {formatPrice(item.price)} · лот {item.public_id}
        </span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sold confirmation
// ---------------------------------------------------------------------------

function soldErrorText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 404) return 'Лот не найден — возможно, уже удалён'
    if (e.status === 409) return 'Лот ещё не опубликован'
    if (e.status === 0) return 'Нет соединения. Проверьте интернет и попробуйте ещё раз.'
  }
  return 'Не удалось отметить продажу. Попробуйте ещё раз.'
}

function SoldConfirm({ item, onConfirm }: { item: AdminItem; onConfirm: () => Promise<void> }) {
  const [sending, setSending] = useState(false)
  return (
    <>
      <LotLine item={item} />
      <h4 className="q">Отметить продажу?</h4>
      <button
        type="button"
        className="btn w100 press"
        disabled={sending}
        onClick={async () => {
          setSending(true)
          await onConfirm()
        }}
      >
        {sending ? 'Отмечаем…' : 'Продано'}
      </button>
    </>
  )
}
