import { createContext, useContext, useEffect, useState } from 'react'
import { Link, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { useMeta } from '../meta'
import { NO_FILTERS, SHOP_NAME, parseFilters } from '../lib'
import { openSellerChat } from '../tg'
import { Search } from './Icons'
import { DeskPalette } from './Palette'

export interface AppCtx {
  inTelegram: boolean
  isAdmin: boolean
}
export const Ctx = createContext<AppCtx>({ inTelegram: false, isAdmin: false })
export const useApp = () => useContext(Ctx)

const IS_MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)

// ---------------------------------------------------------------------------
// Desktop: slim global nav (shop name · «Каталог ⌘K» · seller / admin); the
// trigger or ⌘K / Ctrl+K opens the catalog palette under it.
// ---------------------------------------------------------------------------

export function GlobalNav() {
  const { seller } = useMeta()
  const { isAdmin } = useApp()
  const navigate = useNavigate()
  const location = useLocation()
  const [params] = useSearchParams()
  const [open, setOpen] = useState(false)
  const f = location.pathname === '/' ? parseFilters(params) : NO_FILTERS

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setOpen((o) => !o)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
  // Any navigation (back/forward, a link) closes it.
  useEffect(() => setOpen(false), [location.key])

  return (
    <div>
      <nav className="gnav">
        <div className="gnav-in">
          <button
            type="button"
            className="brand"
            onClick={() => {
              setOpen(false)
              navigate('/')
              window.scrollTo(0, 0)
            }}
          >
            {SHOP_NAME}
          </button>
          <button type="button" className={`c-trig-d ${open ? 'on' : ''}`} onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-keyshortcuts={IS_MAC ? 'Meta+K' : 'Control+K'}>
            <Search />
            <span>Каталог</span>
            <span className="kc">{IS_MAC ? '⌘K' : 'Ctrl K'}</span>
          </button>
          <span className="r">
            {isAdmin ? (
              <Link to="/admin" className="tg" onClick={() => setOpen(false)}>
                Админка
              </Link>
            ) : seller ? (
              <button type="button" className="tg" onClick={() => openSellerChat(seller)}>
                Написать продавцу
              </button>
            ) : null}
          </span>
        </div>
      </nav>
      {open && <DeskPalette f={f} onClose={() => setOpen(false)} />}
    </div>
  )
}
