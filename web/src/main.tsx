import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { initTelegram } from './tg'
import { SHOP_NAME } from './lib'
import './index.css'

const inTelegram = initTelegram()
document.title = SHOP_NAME
// Basename keeps its trailing slash, so the list lives at `/shop/?tags=…`
// (nginx's `/shop` → `/shop/` redirect would drop the query string).
const basename = import.meta.env.BASE_URL
if (basename !== '/' && `${window.location.pathname}/` === basename) {
  window.history.replaceState(window.history.state, '', basename + window.location.search + window.location.hash)
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter basename={basename}>
      <App inTelegram={inTelegram} />
    </BrowserRouter>
  </React.StrictMode>,
)
