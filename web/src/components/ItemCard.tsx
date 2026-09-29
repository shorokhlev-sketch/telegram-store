import { memo } from 'react'
import { Link } from 'react-router-dom'
import type { ItemCard as Card } from '../api'
import { rememberDetail } from '../details'
import { formatPrice, specLine, tidyTitle } from '../lib'
import { ProductImage } from './Art'
import { Play } from './Icons'

/**
 * Seed the ItemPage cache from the card's own data, so opening a lot shows
 * its title/price/cover instantly; ItemPage fetches the full detail behind it.
 */
export function seedDetail(item: Card) {
  rememberDetail({
    ...item,
    year: null,
    kit: [],
    grade_description: null,
    media: item.cover ? [{ kind: 'photo', preview: item.cover, full: item.cover }] : [],
    miniapp_url: '',
  })
}

function VideoBadge() {
  return (
    <span className="vbadge">
      <Play />
      Видео
    </span>
  )
}

/** Phone tile: image on #F5F5F7, name, one grey spec line, price. */
function TileView({ item }: { item: Card }) {
  return (
    <Link to={`/item/${item.public_id}`} onClick={() => seedDetail(item)} className="tile press">
      <div className="tile-img">
        <ProductImage src={item.cover} alt={item.title} device={item.device_type} color={item.color} />
        {item.has_video && <VideoBadge />}
      </div>
      <div className="tile-name">{item.model || tidyTitle(item.title)}</div>
      <div className="tile-spec">{specLine(item)}</div>
      <div className="tile-price num">{formatPrice(item.price)}</div>
    </Link>
  )
}
export const Tile = memo(TileView)

export function TileSkeleton() {
  return (
    <div className="tile sk" aria-hidden="true">
      <div className="tile-img" />
      <div className="sk-line" style={{ width: '80%', marginTop: 12 }} />
      <div className="sk-line" style={{ width: '60%' }} />
      <div className="sk-line" style={{ width: '40%' }} />
    </div>
  )
}

/** Desktop cell on the hairline grid: centered image, «Model, Color», spec, price. */
function CellView({ item }: { item: Card }) {
  return (
    <Link to={`/item/${item.public_id}`} onClick={() => seedDetail(item)} className="dcell">
      <div className="im">
        <ProductImage src={item.cover} alt={item.title} device={item.device_type} color={item.color} />
        {item.has_video && <VideoBadge />}
      </div>
      <div className="nm">
        {item.model || item.title}
        {item.color ? `, ${item.color}` : ''}
      </div>
      <div className="sp">{specLine(item)}</div>
      <div className="pr">{formatPrice(item.price)}</div>
    </Link>
  )
}
export const Cell = memo(CellView)

export function CellSkeleton() {
  return (
    <div className="dcell sk" aria-hidden="true">
      <div className="im">
        <i />
      </div>
      <div className="sk-line" style={{ marginTop: 20 }} />
      <div className="sk-line" />
    </div>
  )
}
