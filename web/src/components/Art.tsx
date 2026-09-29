import { useState } from 'react'
import { colorHex } from '../lib'

/*
 * Product art. Real lots always carry photos; silhouettes (ported from the
 * mockup's neutral inline SVG) cover a missing or broken cover so a tile never
 * shows an empty grey square or a broken-image icon.
 */

let UID = 0

function mix(hex: string, amt: number): string {
  const n = parseInt(hex.slice(1), 16)
  let r = n >> 16
  let g = (n >> 8) & 255
  let b = n & 255
  const t = amt < 0 ? 0 : 255
  const p = Math.abs(amt)
  r = Math.round(r + (t - r) * p)
  g = Math.round(g + (t - g) * p)
  b = Math.round(b + (t - b) * p)
  return `#${((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1)}`
}
const lum = (hex: string) => {
  const n = parseInt(hex.slice(1), 16)
  return (0.3 * (n >> 16) + 0.59 * ((n >> 8) & 255) + 0.11 * (n & 255)) / 255
}

const WALL = ['#1B2230', '#26324A', '#34476A']

function wall(x: number, y: number, w: number, h: number, rx: number, pal = WALL) {
  const id = `w${++UID}`
  return `<defs><clipPath id="${id}"><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}"/></clipPath></defs>
  <g clip-path="url(#${id})"><rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${pal[0]}"/>
  <path d="M${x} ${y + h * 0.58} C${x + w * 0.3} ${y + h * 0.4} ${x + w * 0.58} ${y + h * 0.78} ${x + w} ${y + h * 0.5} V${y + h} H${x} Z" fill="${pal[1]}"/>
  <path d="M${x} ${y + h * 0.8} C${x + w * 0.36} ${y + h * 0.66} ${x + w * 0.66} ${y + h * 0.94} ${x + w} ${y + h * 0.74} V${y + h} H${x} Z" fill="${pal[2]}"/></g>`
}
function lens(cx: number, cy: number, r: number, body: string) {
  return `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${mix(body, -0.28)}"/><circle cx="${cx}" cy="${cy}" r="${r * 0.74}" fill="#131316"/><circle cx="${cx + r * 0.25}" cy="${cy - r * 0.25}" r="${r * 0.2}" fill="#34343A"/>`
}
function phone(h: string) {
  const plate = lum(h) < 0.45 ? mix(h, 0.08) : mix(h, -0.06)
  const cam = `<rect x="65" y="23" width="34" height="34" rx="10" fill="${plate}" class="soft"/>${lens(74, 32, 6.8, h)}${lens(90, 48, 6.8, h)}<circle cx="90" cy="32" r="2.2" fill="#EDE8DC"/>`
  return `<rect x="59" y="16" width="82" height="168" rx="16" fill="${h}" class="edge"/>
  <rect x="57.6" y="50" width="1.6" height="12" rx=".8" fill="${mix(h, -0.2)}"/><rect x="57.6" y="68" width="1.6" height="20" rx=".8" fill="${mix(h, -0.2)}"/><rect x="140.8" y="62" width="1.6" height="28" rx=".8" fill="${mix(h, -0.2)}"/>${cam}`
}
function laptop(h: string) {
  const bezel = '#111113'
  return `<rect x="32" y="42" width="136" height="94" rx="7" fill="${bezel}" class="edge"/>
  <rect x="36.5" y="46.5" width="127" height="85" rx="3" fill="${bezel}"/>${wall(38, 48, 124, 82, 2)}
  <rect x="92.5" y="48" width="15" height="4.2" rx="1.6" fill="${bezel}"/>
  <path d="M18 136.5h164l-3 7.2c-.5 1.3-1.7 2.3-3.2 2.3H24.2c-1.5 0-2.7-1-3.2-2.3z" fill="${mix(h, lum(h) < 0.3 ? 0.1 : -0.04)}" class="edge"/>
  <path d="M86 136.5h28v1.4c0 .9-.7 1.6-1.6 1.6H87.6c-.9 0-1.6-.7-1.6-1.6z" fill="${mix(h, -0.25)}"/>`
}
function tablet(h: string) {
  return `<rect x="45" y="22" width="110" height="156" rx="13" fill="${h}" class="edge"/>
  <rect x="49.5" y="26.5" width="101" height="147" rx="9" fill="#0E0E10"/>${wall(53.5, 30.5, 93, 139, 6, ['#1A2533', '#27405A', '#3B5E80'])}
  <rect x="155" y="44" width="1.8" height="14" rx=".9" fill="${mix(h, -0.2)}"/>`
}
function watch(h: string) {
  const b = mix(h, -0.1)
  return `<path d="M80 12h40l-2.4 44H82.4z" fill="${b}" class="edge"/><path d="M82.4 144h35.2l2.4 44H80z" fill="${b}" class="edge"/>
  <rect x="81" y="164" width="38" height="2" rx="1" fill="${mix(b, -0.25)}"/><rect x="81" y="171" width="38" height="2" rx="1" fill="${mix(b, -0.25)}"/>
  <rect x="63" y="52" width="74" height="96" rx="22" fill="${h}" class="edge"/>
  <rect x="68" y="57" width="64" height="86" rx="17.5" fill="#050506"/>
  <rect x="136" y="76" width="6" height="17" rx="2.8" fill="${mix(h, 0.12)}"/><rect x="136.6" y="100" width="3.6" height="15" rx="1.6" fill="${mix(h, 0.06)}"/>
  <text x="100" y="106" text-anchor="middle" font-family="-apple-system,system-ui" font-size="21" font-weight="600" fill="#F5F5F7" letter-spacing="-.5">10:09</text>`
}
function earbuds() {
  return `<rect x="50" y="58" width="100" height="84" rx="30" fill="#FAFAFA" class="edge"/>
  <path d="M51 84.5h98" class="edge"/><circle cx="100" cy="104" r="2.1" fill="#CFCFD4"/>
  <rect x="150" y="112" width="3" height="11" rx="1.5" fill="#E2E2E6" class="edge"/>
  <rect x="88" y="139.5" width="24" height="2.5" rx="1.25" fill="#D8D8DC"/>`
}
function generic(h: string) {
  return `<rect x="46" y="46" width="108" height="108" rx="24" fill="${h}" class="edge"/><circle cx="100" cy="100" r="16" fill="${mix(h, -0.12)}"/>`
}

/** Cropped viewBoxes for small thumbs (the palette rows): the device fills the square. Laptops stay full-frame. */
const TIGHT: Record<string, string> = {
  phone: '54 12 92 176',
  tablet: '40 18 120 164',
  watch: '54 8 92 184',
  headphones: '44 50 116 100',
  generic: '40 40 120 120',
}

export function Silhouette({ device, color, tight }: { device: string | null; color?: string | null; tight?: boolean }) {
  const h = colorHex(color) ?? '#C9C9CE'
  let body: string
  switch (device) {
    case 'phone':
      body = phone(h)
      break
    case 'laptop':
      body = laptop(h)
      break
    case 'tablet':
      body = tablet(h)
      break
    case 'watch':
      body = watch(h)
      break
    case 'headphones':
      body = earbuds()
      break
    default:
      body = generic(h)
  }
  const box = tight && device !== 'laptop' ? TIGHT[device && TIGHT[device] ? device : 'generic'] : '0 0 200 200'
  return <svg viewBox={box} role="img" aria-hidden="true" className="art" dangerouslySetInnerHTML={{ __html: body }} />
}

/**
 * A product photo on its tile: `.ph` handles the white-background trick
 * (multiply on the light tile). Falls back
 * to the silhouette when there is no photo or it fails to load.
 */
export function ProductImage({
  src,
  alt,
  device,
  color,
  eager,
  tight,
}: {
  src: string | null | undefined
  alt: string
  device: string | null
  color?: string | null
  eager?: boolean
  /** Silhouette fallback cropped to the device (small thumbs). */
  tight?: boolean
}) {
  const [loaded, setLoaded] = useState(false)
  const [failed, setFailed] = useState(false)
  if (!src || failed) return <Silhouette device={device} color={color} tight={tight} />
  return (
    <span className="ph">
      <img
        src={src}
        alt={alt}
        loading={eager ? 'eager' : 'lazy'}
        decoding="async"
        draggable={false}
        className={loaded ? '' : 'ld'}
        onLoad={() => setLoaded(true)}
        onError={() => setFailed(true)}
      />
    </span>
  )
}
