import { useEffect, useMemo, useState } from 'react'
import { Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { fetchAdminMe } from './api'
import { Ctx, GlobalNav } from './components/Nav'
import AdminPage from './pages/AdminPage'
import ItemPage from './pages/ItemPage'
import ListPage from './pages/ListPage'
import { useWide } from './lib'
import { hasInitData, initData, startItemId } from './tg'

/** Mini App deep link `startapp=item_XXXX`: open that lot once, on first load. */
function useStartParam(inTelegram: boolean) {
  const navigate = useNavigate()
  const location = useLocation()
  useEffect(() => {
    if (!inTelegram) return
    const id = startItemId()
    if (!id) return
    const key = `start_param_done:${id}`
    try {
      if (sessionStorage.getItem(key)) return
      sessionStorage.setItem(key, '1')
    } catch {
      /* storage blocked: still navigate once per page load */
    }
    // Push (not replace) so Back from the lot lands on the list.
    if (location.pathname === '/') navigate(`/item/${id}`)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
}

/** Is this user the seller? Only with initData (Mini App from the menu
 * button / t.me links); otherwise the admin-only API is never called. */
function useIsAdmin(): boolean {
  const [isAdmin, setIsAdmin] = useState(false)
  useEffect(() => {
    if (!hasInitData()) return
    const ac = new AbortController()
    fetchAdminMe(initData(), ac.signal)
      .then(() => setIsAdmin(true))
      .catch(() => setIsAdmin(false))
    return () => ac.abort()
  }, [])
  return isAdmin
}

export default function App({ inTelegram }: { inTelegram: boolean }) {
  useStartParam(inTelegram)
  const isAdmin = useIsAdmin()
  const wide = useWide()
  const ctx = useMemo(() => ({ inTelegram, isAdmin }), [inTelegram, isAdmin])

  const routes = (
    <Routes>
      <Route path="/" element={<ListPage />} />
      <Route path="/item/:publicId" element={<ItemPage />} />
      <Route path="/admin" element={<AdminPage />} />
      <Route path="*" element={<ListPage />} />
    </Routes>
  )

  return (
    <Ctx.Provider value={ctx}>
      {wide ? (
        <div className="d-shell">
          <GlobalNav />
          <div className="d-page">{routes}</div>
        </div>
      ) : (
        routes
      )}
    </Ctx.Provider>
  )
}
