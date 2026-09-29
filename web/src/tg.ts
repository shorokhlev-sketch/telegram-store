/**
 * Thin wrapper over window.Telegram.WebApp. Everything here is a no-op on the
 * plain site, so components can call it unconditionally.
 */
import { useEffect, useRef } from 'react'

type ThemeParams = Partial<
  Record<
    | 'bg_color'
    | 'text_color'
    | 'hint_color'
    | 'link_color'
    | 'button_color'
    | 'button_text_color'
    | 'secondary_bg_color',
    string
  >
>

interface BottomButton {
  text: string
  isVisible: boolean
  isActive: boolean
  setText(text: string): BottomButton
  setParams(p: { text?: string; color?: string; text_color?: string; is_active?: boolean; is_visible?: boolean }): BottomButton
  onClick(cb: () => void): BottomButton
  offClick(cb: () => void): BottomButton
  show(): BottomButton
  hide(): BottomButton
  enable(): BottomButton
  disable(): BottomButton
  showProgress(leaveActive?: boolean): BottomButton
  hideProgress(): BottomButton
}

interface ContactResponse {
  status?: string
  responseUnsafe?: { contact?: { phone_number?: string } }
}

export interface TelegramWebApp {
  initData: string
  initDataUnsafe: { start_param?: string; user?: { id: number; first_name?: string; username?: string } }
  version: string
  /** 'android' | 'ios' | 'tdesktop' | 'weba' | 'macos' | ... | 'unknown' when not really inside a Telegram client. */
  platform: string
  colorScheme: 'light' | 'dark'
  themeParams: ThemeParams
  isVersionAtLeast(v: string): boolean
  ready(): void
  expand(): void
  onEvent(event: string, cb: (...args: unknown[]) => void): void
  offEvent(event: string, cb: (...args: unknown[]) => void): void
  setHeaderColor?(color: string): void
  setBackgroundColor?(color: string): void
  setBottomBarColor?(color: string): void
  openTelegramLink?(url: string): void
  requestContact?(cb: (shared: boolean, response?: ContactResponse) => void): void
  /** Sends data to the bot as a `web_app_data` update and closes the Mini App. Only meaningful
   * when opened from a reply-keyboard `web_app` button (empty initData, no HTTP identity). */
  sendData?(data: string): void
  BackButton: { show(): void; hide(): void; onClick(cb: () => void): void; offClick(cb: () => void): void }
  MainButton: BottomButton
  HapticFeedback?: {
    notificationOccurred(type: 'error' | 'success' | 'warning'): void
    impactOccurred(style: 'light' | 'medium' | 'heavy' | 'rigid' | 'soft'): void
    selectionChanged(): void
  }
}

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp }
  }
}

export function webApp(): TelegramWebApp | undefined {
  return window.Telegram?.WebApp
}

/**
 * Running inside an actual Telegram client (any launch method — reply-keyboard
 * button, menu button, inline `web_app` button, t.me link). `platform` is
 * `'unknown'` when the SDK script loaded outside Telegram (plain browser).
 * Use this for UI concerns: theme, header/bottom bar colors, BackButton,
 * openTelegramLink, haptics, hiding MainButton.
 */
export function inTelegramUI(): boolean {
  const wa = webApp()
  return Boolean(wa) && wa!.platform !== 'unknown'
}

/**
 * Telegram handed us non-empty, HMAC-signed `initData` — i.e. we know who the
 * user is. Empty when the Mini App was opened from a reply-keyboard
 * `web_app` button (Bot API quirk). Use this for anything needing identity:
 * lead via the API header, `/api/admin/me`, admin entry.
 */
export function hasInitData(): boolean {
  return Boolean(webApp()?.initData)
}

export function initData(): string {
  return webApp()?.initData ?? ''
}

/**
 * Send lead data to the bot via `web_app_data` (reply-keyboard launch path,
 * no initData). Telegram closes the Mini App right after; the bot confirms
 * in chat. Throws if unsupported.
 */
export function sendLeadData(publicId: string, phone?: string | null): void {
  const wa = webApp()
  if (!wa?.sendData) throw new Error('sendData unsupported')
  wa.sendData(JSON.stringify({ t: 'lead', id: publicId, ...(phone ? { phone } : {}) }))
}

/** `startapp=item_XXXX` deep link → public_id, else null. */
export function startItemId(): string | null {
  const sp = webApp()?.initDataUnsafe?.start_param
  const m = sp ? /^item_([A-Za-z0-9]+)$/.exec(sp) : null
  return m ? m[1] : null
}

// ---------------------------------------------------------------------------
// Theme: light only (Lev 28.09: «дневной режим по умолчанию, ночной не нужен»).
// Telegram's dark colorScheme is ignored; header/background/bottom bar are
// painted white so the Mini App looks the same in every client.
// ---------------------------------------------------------------------------

const PAGE_BG = '#FFFFFF'

function applyTheme(wa: TelegramWebApp) {
  for (const set of [wa.setHeaderColor, wa.setBackgroundColor, wa.setBottomBarColor]) {
    try {
      set?.call(wa, PAGE_BG)
    } catch {
      /* older clients */
    }
  }
}

/** Call once at startup. Returns true when running inside a Telegram client. */
export function initTelegram(): boolean {
  const wa = webApp()
  if (!wa || !inTelegramUI()) return false
  document.documentElement.classList.add('tg')
  applyTheme(wa)
  // The in-page action bar replaces Telegram's MainButton everywhere.
  try {
    wa.MainButton.hide()
  } catch {
    /* unsupported */
  }
  wa.ready()
  wa.expand()
  return true
}

/** Open the seller chat: inside Telegram without leaving the client, else a new tab. */
export function openSellerChat(url: string) {
  const wa = webApp()
  if (inTelegramUI() && wa?.openTelegramLink && /^https:\/\/t\.me\//.test(url)) {
    try {
      wa.openTelegramLink(url)
      return
    } catch {
      /* fall through */
    }
  }
  window.open(url, '_blank', 'noopener,noreferrer')
}

export function haptic(type: 'success' | 'error' | 'warning') {
  try {
    webApp()?.HapticFeedback?.notificationOccurred(type)
  } catch {
    /* unsupported */
  }
}

// ---------------------------------------------------------------------------
// BackButton: a stack, so a sheet/lightbox opened over the item page takes
// over the button and hands it back on close.
// ---------------------------------------------------------------------------

const backStack: Array<{ cb: () => void }> = []
let backBound = false

function onBackClick() {
  backStack[backStack.length - 1]?.cb()
}

function syncBack() {
  const wa = webApp()
  if (!wa || !inTelegramUI()) return
  if (!backBound) {
    wa.BackButton.onClick(onBackClick)
    backBound = true
  }
  if (backStack.length) wa.BackButton.show()
  else wa.BackButton.hide()
}

/** Show Telegram's BackButton while mounted/active; `handler` is called on tap. */
export function useBackButton(handler: () => void, active = true) {
  const ref = useRef(handler)
  ref.current = handler
  useEffect(() => {
    if (!active || !inTelegramUI()) return
    const entry = { cb: () => ref.current() }
    backStack.push(entry)
    syncBack()
    return () => {
      const i = backStack.indexOf(entry)
      if (i >= 0) backStack.splice(i, 1)
      syncBack()
    }
  }, [active])
}

/** Request the user's phone via Telegram. Resolves null if declined/unsupported. */
export function requestPhone(): Promise<string | null> {
  const wa = webApp()
  if (!wa?.requestContact || !wa.isVersionAtLeast('6.9')) return Promise.resolve(null)
  return new Promise((resolve) => {
    try {
      wa.requestContact!((shared, response) => {
        if (!shared) return resolve(null)
        const phone = response?.responseUnsafe?.contact?.phone_number
        resolve(phone ? (phone.startsWith('+') ? phone : `+${phone}`) : null)
      })
    } catch {
      resolve(null)
    }
  })
}

export function canRequestPhone(): boolean {
  const wa = webApp()
  return Boolean(wa?.requestContact && wa.isVersionAtLeast('6.9'))
}
