import { useCallback, useRef, useState, type FormEvent } from 'react'
import { ApiError, postLead, type ItemDetail } from '../api'
import { formatPrice, memStr } from '../lib'
import { canRequestPhone, haptic, hasInitData, initData, requestPhone, sendLeadData } from '../tg'
import { ProductImage } from './Art'
import { CheckS, OkBig } from './Icons'
import { Dialog } from './Sheet'

export interface LeadSheetProps {
  item: ItemDetail
  inTelegram: boolean
  /** Set once a lead went through: the sheet then shows the success state. */
  sent: boolean
  onSent: () => void
  onClose: () => void
  onSold: () => void
}

function leadErrorText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 429) return 'Слишком много заявок. Попробуйте позже.'
    if (e.status === 401) return 'Не удалось подтвердить вход через Telegram. Закройте и откройте приложение заново.'
    if (e.status === 0) return 'Нет соединения. Проверьте интернет и попробуйте ещё раз.'
    if (e.status === 422 && typeof e.detail === 'string') return e.detail
  }
  return 'Не удалось отправить заявку. Попробуйте ещё раз.'
}

/**
 * «Купить» — one question per screen. Three paths:
 * - Mini App with initData (menu button, t.me links): confirm + optional
 *   phone via Telegram → POST with initData.
 * - Mini App without initData (opened from the bot's reply-keyboard button,
 *   Telegram hands out empty initData there): same screen, the button calls
 *   `Telegram.WebApp.sendData(...)`; the bot picks up `web_app_data` and
 *   confirms in chat, Telegram closes the app.
 * - Site: name + contact in one bordered group → POST (honeypot included).
 */
export default function LeadSheet(props: LeadSheetProps) {
  const { sent, onClose } = props
  let body
  if (sent) body = <Success onDone={onClose} />
  else if (!props.inTelegram) body = <SiteLead {...props} />
  else body = <TelegramLead {...props} withId={hasInitData()} />
  return (
    <Dialog onClose={onClose} label={sent ? 'Заявка отправлена' : 'Заявка продавцу'}>
      <div className="buy">{body}</div>
    </Dialog>
  )
}

function LotLine({ item }: { item: ItemDetail }) {
  const mem = memStr(item) || item.color || ''
  return (
    <div className="lotline">
      <div className="th">
        <ProductImage src={item.cover} alt="" device={item.device_type} color={item.color} eager />
      </div>
      <div>
        <b>
          {item.model || item.title}
          {mem ? `, ${mem}` : ''}
        </b>
        <span>
          {formatPrice(item.price)} · лот {item.public_id}
        </span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Telegram (with or without initData)
// ---------------------------------------------------------------------------

function TelegramLead({ item, onSent, onSold, withId }: LeadSheetProps & { withId: boolean }) {
  const [sending, setSending] = useState(false)
  const [phone, setPhone] = useState<string | null>(null)
  const [phoneOn, setPhoneOn] = useState(canRequestPhone())
  const [error, setError] = useState<string | null>(null)

  const send = async () => {
    setError(null)
    setSending(true)
    if (!withId) {
      try {
        sendLeadData(item.public_id, phone)
        // Telegram closes the Mini App here on success; the bot confirms in chat.
      } catch {
        haptic('error')
        setSending(false)
        setError('Не удалось отправить заявку. Попробуйте ещё раз.')
      }
      return
    }
    try {
      await postLead(item.public_id, phone ? { phone } : {}, initData())
      haptic('success')
      onSent()
    } catch (e) {
      haptic('error')
      if (e instanceof ApiError && e.status === 410) {
        onSold()
        return
      }
      setError(leadErrorText(e))
    } finally {
      setSending(false)
    }
  }

  const askPhone = async () => {
    const p = await requestPhone()
    if (p) {
      setPhone(p)
      haptic('success')
    } else {
      // Declined or unsupported: drop the row, the lead goes without a phone.
      setPhoneOn(false)
    }
  }

  return (
    <>
      <LotLine item={item} />
      <h4 className="q">Отправить заявку?</h4>
      {phoneOn && (
        <div className="fields">
          {phone ? (
            <div className="field act">
              <span className="t">
                <small>Телефон</small>
                <b className="set num">{phone}</b>
              </span>
              <span className="ok">
                <CheckS />
              </span>
            </div>
          ) : (
            <button type="button" className="field act" onClick={askPhone} disabled={sending}>
              <span className="t">
                <small>Телефон</small>
                <b>Поделиться телефоном</b>
              </span>
            </button>
          )}
        </div>
      )}
      {error && <div className="ferr">{error}</div>}
      <div className="gap" />
      <button type="button" className="btn w100 press" onClick={send} disabled={sending}>
        {sending ? 'Отправляем…' : 'Отправить заявку'}
      </button>
    </>
  )
}

// ---------------------------------------------------------------------------
// Site
// ---------------------------------------------------------------------------

const LS_KEY = 'lead_contact_v1'

function loadSaved(): { name: string; contact: string } {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw) {
      const v = JSON.parse(raw)
      return { name: String(v.name ?? ''), contact: String(v.contact ?? '') }
    }
  } catch {
    /* storage unavailable */
  }
  return { name: '', contact: '' }
}

type FieldErrors = Partial<Record<'name' | 'contact' | 'form', string>>

function SiteLead({ item, onSent, onSold }: LeadSheetProps) {
  const saved = useRef(loadSaved()).current
  const [name, setName] = useState(saved.name)
  const [contact, setContact] = useState(saved.contact)
  const [website, setWebsite] = useState('') // honeypot
  const [errors, setErrors] = useState<FieldErrors>({})
  const [sending, setSending] = useState(false)

  const submit = useCallback(
    async (e: FormEvent) => {
      e.preventDefault()
      const n = name.trim()
      const c = contact.trim()
      const errs: FieldErrors = {}
      if (n.length < 1) errs.name = 'Напишите, как к вам обращаться.'
      else if (n.length > 128) errs.name = 'Слишком длинное имя.'
      if (c.length < 3) errs.contact = 'Укажите телефон или @ник, чтобы продавец мог ответить.'
      else if (c.length > 128) errs.contact = 'Слишком длинный контакт.'
      setErrors(errs)
      if (Object.keys(errs).length) return

      setSending(true)
      try {
        await postLead(item.public_id, { name: n, contact: c, website })
        try {
          localStorage.setItem(LS_KEY, JSON.stringify({ name: n, contact: c }))
        } catch {
          /* storage unavailable */
        }
        onSent()
      } catch (err) {
        if (err instanceof ApiError && err.status === 410) {
          onSold()
          return
        }
        if (err instanceof ApiError && err.status === 422 && Array.isArray(err.detail)) {
          const fe: FieldErrors = {}
          for (const d of err.detail as { loc?: unknown[] }[]) {
            const field = d.loc?.[d.loc.length - 1]
            if (field === 'name' || field === 'contact') fe[field] = 'Проверьте это поле.'
            else fe.form = 'Проверьте поля формы.'
          }
          setErrors(fe)
        } else {
          setErrors({ form: leadErrorText(err) })
        }
      } finally {
        setSending(false)
      }
    },
    [name, contact, website, item.public_id, onSent, onSold],
  )

  const errText = errors.contact ?? errors.name ?? errors.form
  return (
    <>
      <LotLine item={item} />
      <h4 className="q">Оставьте контакт.</h4>
      <form onSubmit={submit} noValidate>
        <div className="fields">
          <label className={`field ${errors.name ? 'bad' : ''}`}>
            <small>Имя</small>
            <input
              name="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Как к вам обращаться"
              autoComplete="name"
              maxLength={128}
              aria-invalid={Boolean(errors.name)}
            />
          </label>
          <label className={`field ${errors.contact ? 'bad' : ''}`}>
            <small>Телефон или @ник в Telegram</small>
            <input
              name="contact"
              value={contact}
              onChange={(e) => setContact(e.target.value)}
              placeholder="+7 900 000-00-00 или @nick"
              autoComplete="tel"
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
              maxLength={128}
              aria-invalid={Boolean(errors.contact)}
            />
          </label>
        </div>
        {/* Honeypot: bots fill every field; humans never see this one. */}
        <input className="hp" name="website" tabIndex={-1} autoComplete="off" aria-hidden="true" value={website} onChange={(e) => setWebsite(e.target.value)} />
        {errText && <div className="ferr">{errText}</div>}
        <div className="gap" />
        <button className="btn w100 press" type="submit" disabled={sending}>
          {sending ? 'Отправляем…' : 'Отправить заявку'}
        </button>
      </form>
    </>
  )
}

function Success({ onDone }: { onDone: () => void }) {
  return (
    <div className="done">
      <div className="okc">
        <OkBig />
      </div>
      <h4>Заявка отправлена.</h4>
      <button type="button" className="btn tint w100 press" onClick={onDone}>
        Готово
      </button>
    </div>
  )
}
