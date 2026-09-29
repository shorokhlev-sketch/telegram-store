import { useEffect, useMemo, useState } from 'react'
import {
  adminFieldError,
  ApiError,
  fetchAdminCatalog,
  patchAdminItem,
  postAdminCover,
  type AdminCatalog,
  type AdminCategory,
  type AdminItem,
  type AdminModel,
  type AdminPatch,
  type AdminPatchResult,
} from '../../api'
import { GRADE, storageLabel } from '../../lib'
import { haptic, initData } from '../../tg'
import { ProductImage } from '../Art'
import { Check, ChevR } from '../Icons'
import Sheet from '../Sheet'
import './admin-edit.css'

/*
 * Full lot editor for the Mini App admin. Mirrors the bot wizard: category
 * first, then either Apple pickers (model -> storage -> option -> color,
 * single-choice options filled automatically) or typed fields for «Другое».
 * Only changed fields go to PATCH; a category/model change also sends every
 * field that depends on it, because the server resets them.
 */

const nf = new Intl.NumberFormat('ru-RU')
const OTHER = 'other'
const APPLE_BRANCH = ['model', 'storage_gb', 'variant', 'color'] as const
const OTHER_BRANCH = ['device_type', 'brand', 'model', 'year', 'storage_gb', 'color'] as const

interface Form {
  category: string
  device_type: string | null
  brand: string
  model: string
  year: string
  storage_gb: number | null
  variant: string | null
  color: string
  battery: string
  batteryNone: boolean
  grade: string | null
  kit: string[]
  price: string
  note: string
}

type Values = Required<AdminPatch>
type Errors = Partial<Record<string, string>>

function formOf(item: AdminItem): Form {
  return {
    category: item.category,
    device_type: item.device_type,
    brand: item.brand ?? '',
    model: item.model ?? '',
    year: item.year != null ? String(item.year) : '',
    storage_gb: item.storage_gb,
    variant: item.variant,
    color: item.color ?? '',
    battery: item.battery_health != null ? String(item.battery_health) : '',
    batteryNone: item.battery_health == null,
    grade: item.grade,
    kit: item.kit,
    price: item.price != null ? String(item.price) : '',
    note: item.note ?? '',
  }
}

const squash = (s: string) => s.split(/\s+/).filter(Boolean).join(' ')

/** Every editable field as the API wants it (Apple lots: derived fields left to the server). */
function valuesOf(f: Form, cat: AdminCatalog): Partial<Values> {
  const order = cat.kit.map((k) => k.code)
  const common = {
    category: f.category,
    battery_health: f.batteryNone || f.battery === '' ? null : Number(f.battery),
    grade: f.grade ?? '',
    kit: order.filter((c) => f.kit.includes(c)),
    price: f.price ? Number(f.price) : 0,
    note: f.note.trim(),
  }
  if (f.category === OTHER)
    return {
      ...common,
      device_type: f.device_type,
      brand: squash(f.brand) || null,
      model: squash(f.model) || null,
      year: f.year ? Number(f.year) : null,
      storage_gb: f.storage_gb,
      color: squash(f.color) || null,
    }
  return { ...common, model: f.model || null, storage_gb: f.storage_gb, variant: f.variant, color: f.color || null }
}

const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null)

function buildPatch(now: Partial<Values>, was: Partial<Values>): AdminPatch {
  const patch: Record<string, unknown> = {}
  for (const k of Object.keys(now) as (keyof Values)[]) if (!same(now[k], was[k])) patch[k] = now[k]
  // The server resets dependent fields on a category/model change: resend them.
  const deps: readonly string[] = 'category' in patch ? (now.category === OTHER ? OTHER_BRANCH : APPLE_BRANCH) : 'model' in patch && now.category !== OTHER ? APPLE_BRANCH : []
  for (const k of deps) patch[k] = now[k as keyof Values]
  return patch as AdminPatch
}

const VARIANT_PROMPT: Record<string, string> = {
  'Оперативная память': 'Выберите оперативную память',
  'Размер корпуса': 'Выберите размер корпуса',
}

/** Same required-field rules and messages as the server (services/lot_edit.py). */
function validate(f: Form, cat: AdminCatalog, category: AdminCategory | undefined, spec: AdminModel | undefined): Errors {
  const e: Errors = {}
  const { limits } = cat
  if (!category) e.category = 'Нет такой категории'
  else if (category.manual) {
    if (!f.device_type) e.device_type = 'Выберите тип устройства'
    if (!squash(f.brand)) e.brand = 'Укажите производителя'
    if (!squash(f.model)) e.model = 'Укажите модель'
    if (!f.year) e.year = 'Укажите год выпуска'
    else if (Number(f.year) < limits.year_min || Number(f.year) > limits.year_max) e.year = `Нужен год от ${limits.year_min} до ${limits.year_max}`
  } else if (!f.model) e.model = 'Выберите модель'
  else if (spec) {
    if (category.has_storage && spec.storages.length && f.storage_gb == null) e.storage_gb = 'Выберите память'
    if (spec.variants.length && !f.variant) e.variant = VARIANT_PROMPT[category.variant_label ?? ''] ?? 'Выберите вариант'
    if (spec.colors.length && !f.color) e.color = 'Выберите цвет'
  }
  if (!f.batteryNone || !category?.battery_skippable) {
    if (f.battery === '') e.battery_health = 'Укажите ёмкость аккумулятора'
    else if (Number(f.battery) > 100) e.battery_health = 'Нужно целое число от 0 до 100'
  }
  if (!f.grade) e.grade = 'Выберите состояние'
  if (!f.price || Number(f.price) <= 0) e.price = 'Укажите цену'
  else if (Number(f.price) > limits.price_max) e.price = 'Слишком большая цена'
  return e
}

function saveErrorText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 409) return typeof e.detail === 'string' ? `${e.detail}.` : 'Лот уже продан.'
    if (e.status === 404) return 'Лот не найден.'
    if (e.status === 0) return 'Нет соединения. Попробуйте ещё раз.'
  }
  return 'Не удалось сохранить. Попробуйте ещё раз.'
}

// ---------------------------------------------------------------------------

interface Props {
  item: AdminItem
  onClose: () => void
  /** Saved: the sheet closes, the board updates the row and shows the toast. */
  onSaved: (result: AdminPatchResult) => void
  /** Cover changed: the row updates, the sheet stays open. */
  onCover: (result: AdminPatchResult) => void
}

export default function LotEditSheet({ item, onClose, onSaved, onCover }: Props) {
  const [cat, setCat] = useState<AdminCatalog | null>(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let live = true
    setLoadFailed(false)
    fetchAdminCatalog(initData())
      .then((c) => live && setCat(c))
      .catch(() => live && setLoadFailed(true))
    return () => {
      live = false
    }
  }, [attempt])

  const [form, setForm] = useState<Form>(() => formOf(item))
  const [errors, setErrors] = useState<Errors>({})
  const [showErrors, setShowErrors] = useState(false)
  const [serverError, setServerError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [picker, setPicker] = useState<PickerSpec | null>(null)

  const initial = useMemo(() => formOf(item), [item.public_id]) // eslint-disable-line react-hooks/exhaustive-deps
  const category = cat?.categories.find((c) => c.code === form.category)
  const models = cat?.models[form.category] ?? []
  const spec = models.find((m) => m.name === form.model)

  const patch = useMemo(() => (cat ? buildPatch(valuesOf(form, cat), valuesOf(initial, cat)) : {}), [cat, form, initial])
  const dirty = Object.keys(patch).length > 0

  // Live re-validation once the admin has tried to save.
  useEffect(() => {
    if (cat && showErrors) setErrors(validate(form, cat, category, spec))
  }, [cat, form, showErrors, category, spec])

  const set = (p: Partial<Form>) => {
    setServerError(null)
    setForm((f) => ({ ...f, ...p }))
  }

  const pickCategory = (code: string) => {
    if (!cat || code === form.category) return
    const next = cat.categories.find((c) => c.code === code)
    let p: Partial<Form>
    if (code === initial.category) {
      p = { category: code, device_type: initial.device_type, brand: initial.brand, model: initial.model, year: initial.year, storage_gb: initial.storage_gb, variant: initial.variant, color: initial.color }
    } else {
      p = { category: code, device_type: next?.device_type ?? null, brand: '', model: '', year: '', storage_gb: null, variant: null, color: '' }
    }
    if (next && !next.battery_skippable) p.batteryNone = false
    set(p)
  }

  const pickModel = (name: string) => {
    if (!category || name === form.model) return
    if (form.category === initial.category && name === initial.model) {
      set({ model: name, storage_gb: initial.storage_gb, variant: initial.variant, color: initial.color })
      return
    }
    const m = models.find((x) => x.name === name)
    set({
      model: name,
      storage_gb: category.has_storage && m?.storages.length === 1 ? m.storages[0] : null,
      variant: m?.variants.length === 1 ? m.variants[0] : null,
      color: m?.colors.length === 1 ? m.colors[0] : '',
    })
  }

  const save = async () => {
    if (!cat || !dirty || sending) return
    const errs = validate(form, cat, category, spec)
    setShowErrors(true)
    setErrors(errs)
    if (Object.keys(errs).length) {
      haptic('error')
      revealError()
      return
    }
    setSending(true)
    setServerError(null)
    try {
      onSaved(await patchAdminItem(item.public_id, patch, initData()))
    } catch (e) {
      haptic('error')
      const fe = adminFieldError(e)
      if (fe && fe.field in FIELD_ROWS) setErrors({ [fe.field]: fe.message })
      else setServerError(fe ? fe.message : saveErrorText(e))
      setSending(false)
      revealError()
    }
  }

  const err = (k: string) => errors[k]

  // Bring the first problem into view (the form is taller than the screen).
  const revealError = () =>
    requestAnimationFrame(() => document.querySelector('.le-body .bad, .le-body .le-err, .le-foot .ferr')?.scrollIntoView({ block: 'center', behavior: 'smooth' }))

  const footer = (
    <div className="le-foot">
      {serverError && <div className="ferr">{serverError}</div>}
      <button type="button" className="btn w100 press" disabled={!dirty || sending || !cat} onClick={save}>
        {sending ? 'Сохраняем…' : 'Сохранить'}
      </button>
    </div>
  )

  return (
    <Sheet onClose={onClose} label={`Лот ${item.public_id}`} title={`Лот ${item.public_id}`} footer={footer} full>
      <div className="sheet-body le-body">
        <CoverStrip item={item} onCover={onCover} />

        {!cat && !loadFailed && <LoadingGroups />}
        {loadFailed && (
          <div className="empty">
            <p>Не удалось загрузить.</p>
            <button type="button" className="more" onClick={() => setAttempt((n) => n + 1)}>
              Повторить
            </button>
          </div>
        )}

        {cat && (
          <>
            <h4 className="le-h">Устройство</h4>
            <div className="group">
              <PickRow k="Категория" value={category?.label ?? form.category} bad={err('category')} onClick={() => setPicker({ title: 'Категория', options: cat.categories.map((c) => ({ value: c.code, label: c.label })), selected: form.category, onPick: (v) => pickCategory(String(v)) })} />
              {category?.manual ? (
                <>
                  <PickRow k="Тип устройства" value={cat.device_types.find((d) => d.code === form.device_type)?.label} bad={err('device_type')} onClick={() => setPicker({ title: 'Тип устройства', options: cat.device_types.map((d) => ({ value: d.code, label: d.label })), selected: form.device_type, onPick: (v) => set({ device_type: String(v) }) })} />
                  <TextRow k="Производитель" value={form.brand} placeholder="Samsung" max={cat.limits.text_max} bad={err('brand')} onChange={(v) => set({ brand: v })} />
                  <TextRow k="Модель" value={form.model} placeholder="Galaxy S23" max={cat.limits.text_max} bad={err('model')} onChange={(v) => set({ model: v })} />
                  <TextRow k="Год" value={form.year} placeholder="2023" max={4} numeric bad={err('year')} onChange={(v) => set({ year: v.replace(/\D/g, '') })} />
                  <PickRow
                    k="Память"
                    value={form.storage_gb != null ? storageLabel(form.storage_gb) : 'Не указано'}
                    muted={form.storage_gb == null}
                    bad={err('storage_gb')}
                    onClick={() => setPicker({ title: 'Память', options: [{ value: null, label: 'Не указано' }, ...cat.other_storages.map((s) => ({ value: s, label: storageLabel(s) ?? String(s) }))], selected: form.storage_gb, onPick: (v) => set({ storage_gb: v as number | null }) })}
                  />
                  <TextRow k="Цвет" value={form.color} placeholder="Не указан" max={cat.limits.text_max} bad={err('color')} onChange={(v) => set({ color: v })} />
                </>
              ) : (
                category && (
                  <>
                    <PickRow k="Модель" value={form.model || undefined} bad={err('model')} onClick={() => setPicker({ title: 'Модель', options: models.map((m) => ({ value: m.name, label: m.name })), selected: form.model, onPick: (v) => pickModel(String(v)) })} />
                    {category.has_storage && (!spec || spec.storages.length > 0) && (
                      <OptionRow k="Память" value={form.storage_gb} options={spec?.storages} label={(s) => storageLabel(s) ?? String(s)} disabled={!spec} bad={err('storage_gb')} onOpen={(options) => setPicker({ title: 'Память', options, selected: form.storage_gb, onPick: (v) => set({ storage_gb: v as number }) })} />
                    )}
                    {category.variant_label && (!spec || spec.variants.length > 0) && (
                      <OptionRow k={category.variant_label} value={form.variant} options={spec?.variants} label={String} disabled={!spec} bad={err('variant')} onOpen={(options) => setPicker({ title: category.variant_label ?? '', options, selected: form.variant, onPick: (v) => set({ variant: String(v) }) })} />
                    )}
                    <OptionRow k="Цвет" value={form.color || null} options={spec?.colors} label={String} disabled={!spec} bad={err('color')} onOpen={(options) => setPicker({ title: 'Цвет', options, selected: form.color, onPick: (v) => set({ color: String(v) }) })} />
                  </>
                )
              )}
            </div>
            <Errs list={['category', 'device_type', 'brand', 'model', 'year', 'storage_gb', 'variant', 'color'].map(err)} />

            <h4 className="le-h">Аккумулятор</h4>
            <div className="group">
              <label className={`le-row ${err('battery_health') ? 'bad' : ''}`}>
                <span className="k">Ёмкость</span>
                <input
                  value={form.batteryNone && category?.battery_skippable ? '' : form.battery}
                  onChange={(e) => set({ battery: e.target.value.replace(/\D/g, '').slice(0, 3) })}
                  inputMode="numeric"
                  placeholder={form.batteryNone && category?.battery_skippable ? 'Не указано' : '92'}
                  disabled={form.batteryNone && category?.battery_skippable}
                  className="num"
                  aria-invalid={Boolean(err('battery_health'))}
                />
                {!(form.batteryNone && category?.battery_skippable) && <span className="u">%</span>}
              </label>
              {category?.battery_skippable && (
                <button type="button" role="switch" aria-checked={form.batteryNone} className="le-row" onClick={() => set({ batteryNone: !form.batteryNone })}>
                  <span className="l">Не указано</span>
                  <span className={`sw ${form.batteryNone ? 'on' : ''}`} aria-hidden="true" />
                </button>
              )}
            </div>
            <Errs list={[err('battery_health')]} />

            <h4 className="le-h">Состояние</h4>
            <div className={`le-seg ${err('grade') ? 'bad' : ''}`}>
              <div className="seg" role="radiogroup" aria-label="Состояние">
                {cat.grades.map((g) => (
                  <button key={g.code} type="button" role="radio" aria-checked={form.grade === g.code} className={form.grade === g.code ? 'on' : ''} onClick={() => set({ grade: g.code })}>
                    <span className="lt">{g.code}</span>
                    {GRADE[g.code]?.seg ?? g.label}
                  </button>
                ))}
              </div>
            </div>
            <Errs list={[err('grade')]} />

            <h4 className="le-h">Комплект</h4>
            <div className="group">
              {cat.kit.map((k) => {
                const on = form.kit.includes(k.code)
                return (
                  <button key={k.code} type="button" role="checkbox" aria-checked={on} className="le-row" onClick={() => set({ kit: on ? form.kit.filter((c) => c !== k.code) : [...form.kit, k.code] })}>
                    <span className="l">{k.label}</span>
                    <span className="ck">{on && <Check />}</span>
                  </button>
                )
              })}
            </div>
            <Errs list={[err('kit')]} />

            <h4 className="le-h">Цена</h4>
            <div className="group">
              <label className={`le-row ${err('price') ? 'bad' : ''}`}>
                <span className="k">Цена</span>
                <input
                  value={form.price ? nf.format(Number(form.price)) : ''}
                  onChange={(e) => set({ price: e.target.value.replace(/\D/g, '').replace(/^0+/, '').slice(0, 9) })}
                  inputMode="numeric"
                  placeholder="69 000"
                  className="num"
                  aria-invalid={Boolean(err('price'))}
                />
                <span className="u">₽</span>
              </label>
            </div>
            <Errs list={[err('price')]} />

            <h4 className="le-h">Заметка</h4>
            <div className="group le-ta">
              <textarea value={form.note} onChange={(e) => set({ note: e.target.value })} maxLength={cat.limits.note_max} rows={4} placeholder="Заметка к лоту" aria-label="Заметка" />
            </div>
            <Errs list={[err('note')]} />
          </>
        )}
      </div>
      {picker && <Picker spec={picker} onClose={() => setPicker(null)} />}
    </Sheet>
  )
}

/** Fields that have a row in the form (server 422s on these show inline). */
const FIELD_ROWS: Record<string, true> = {
  category: true, device_type: true, brand: true, model: true, year: true, storage_gb: true, variant: true, color: true,
  battery_health: true, grade: true, kit: true, price: true, note: true,
}

// ---------------------------------------------------------------------------
// Rows
// ---------------------------------------------------------------------------

function Errs({ list }: { list: (string | undefined)[] }) {
  const msgs = list.filter(Boolean)
  if (!msgs.length) return null
  return (
    <div className="le-err" role="alert">
      {msgs.map((m, i) => (
        <div key={i}>{m}</div>
      ))}
    </div>
  )
}

function PickRow({ k, value, bad, muted, onClick }: { k: string; value?: string | null; bad?: string; muted?: boolean; onClick: () => void }) {
  return (
    <button type="button" className={`le-row ${bad ? 'bad' : ''}`} onClick={onClick}>
      <span className="k">{k}</span>
      <span className={`v ${!value || muted ? 'none' : ''}`}>{value || 'Не выбрано'}</span>
      <span className="chev">
        <ChevR />
      </span>
    </button>
  )
}

/** Apple option row: a picker when the model offers a choice, static text when it has one value. */
function OptionRow<T extends string | number>({
  k,
  value,
  options,
  label,
  disabled,
  bad,
  onOpen,
}: {
  k: string
  value: T | null
  options?: T[]
  label: (v: T) => string
  disabled: boolean
  bad?: string
  onOpen: (options: PickOption[]) => void
}) {
  const text = value != null && value !== '' ? label(value) : undefined
  if (disabled || !options || options.length <= 1)
    return (
      <div className={`le-row off ${bad ? 'bad' : ''}`}>
        <span className="k">{k}</span>
        <span className={`v ${text ? '' : 'none'}`}>{text ?? 'Не выбрано'}</span>
      </div>
    )
  return <PickRow k={k} value={text} bad={bad} onClick={() => onOpen(options.map((o) => ({ value: o, label: label(o) })))} />
}

function TextRow({ k, value, placeholder, max, numeric, bad, onChange }: { k: string; value: string; placeholder: string; max: number; numeric?: boolean; bad?: string; onChange: (v: string) => void }) {
  return (
    <label className={`le-row ${bad ? 'bad' : ''}`}>
      <span className="k">{k}</span>
      <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} maxLength={max} inputMode={numeric ? 'numeric' : undefined} className={numeric ? 'num' : undefined} aria-invalid={Boolean(bad)} />
    </label>
  )
}

function LoadingGroups() {
  return (
    <div className="le-load" aria-busy="true">
      <h4 className="le-h">Устройство</h4>
      <div className="group">
        {[60, 45, 30, 40].map((w, i) => (
          <div className="le-row" key={i}>
            <div className="sk-line" style={{ width: `${w}%` }} />
          </div>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Cover strip
// ---------------------------------------------------------------------------

function CoverStrip({ item, onCover }: { item: AdminItem; onCover: (result: AdminPatchResult) => void }) {
  const [photos, setPhotos] = useState(item.photos)
  const [sel, setSel] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  if (!photos.length) return null
  const cover = photos[0].file_unique_id

  const makeCover = async () => {
    if (!sel || busy) return
    setBusy(true)
    setError(null)
    try {
      const res = await postAdminCover(item.public_id, sel, initData())
      setPhotos(res.photos)
      setSel(null)
      onCover(res)
    } catch (e) {
      haptic('error')
      setError(adminFieldError(e)?.message ?? saveErrorText(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <h4 className="le-h">Фото</h4>
      <div className="le-photos hs" role="listbox" aria-label="Фото">
        {photos.map((p, i) => {
          const isCover = p.file_unique_id === cover
          const on = sel === p.file_unique_id
          return (
            <button
              key={p.file_unique_id}
              type="button"
              role="option"
              aria-selected={on || (!sel && isCover)}
              className={`le-ph press ${on || (!sel && isCover) ? 'sel' : ''} ${isCover ? 'cover' : ''}`}
              onClick={() => setSel(isCover ? null : p.file_unique_id)}
            >
              <span className="im">
                <ProductImage src={p.preview} alt={`Фото ${i + 1}`} device={item.device_type} eager />
              </span>
              <small>{isCover ? 'Обложка' : i + 1}</small>
            </button>
          )
        })}
      </div>
      {sel && (
        <div className="le-cover-act">
          <button type="button" className="btn tint sm press" disabled={busy} onClick={makeCover}>
            {busy ? 'Меняем…' : 'Сделать обложкой'}
          </button>
        </div>
      )}
      {error && <div className="le-err">{error}</div>}
    </>
  )
}

// ---------------------------------------------------------------------------
// Picker: nested sheet, list with a checkmark
// ---------------------------------------------------------------------------

type PickValue = string | number | null
interface PickOption {
  value: PickValue
  label: string
}
interface PickerSpec {
  title: string
  options: PickOption[]
  selected: PickValue
  onPick: (value: PickValue) => void
}

function Picker({ spec, onClose }: { spec: PickerSpec; onClose: () => void }) {
  // Escape closes only this sheet, not the editor under it: capture on window
  // runs before the editor's own (bubbling) Escape handler and stops it.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.stopPropagation()
      onClose()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [onClose])

  return (
    <Sheet onClose={onClose} label={spec.title} title={spec.title}>
      <div className="sheet-body le-body">
        <div className="group" role="listbox" aria-label={spec.title}>
          {spec.options.map((o) => {
            const on = o.value === spec.selected || (o.value === null && spec.selected == null)
            return (
              <button
                key={String(o.value)}
                type="button"
                role="option"
                aria-selected={on}
                className="le-row"
                onClick={() => {
                  spec.onPick(o.value)
                  onClose()
                }}
              >
                <span className="l">{o.label}</span>
                <span className="ck">{on && <Check />}</span>
              </button>
            )
          })}
        </div>
      </div>
    </Sheet>
  )
}
