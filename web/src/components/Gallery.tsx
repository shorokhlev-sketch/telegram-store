import { useCallback, useEffect, useRef, useState } from 'react'
import type { MediaItem } from '../api'
import { formatDuration, useScrollLock } from '../lib'
import { useBackButton } from '../tg'
import { ProductImage, Silhouette } from './Art'
import { Play, X } from './Icons'

interface Props {
  media: MediaItem[]
  title: string
  device: string | null
  color: string | null
}

/** Video slide: poster placeholder until tapped, then an inline player (playsinline, sound on user tap). */
function VideoFrame({ m, active }: { m: MediaItem; active: boolean }) {
  const [started, setStarted] = useState(false)
  const ref = useRef<HTMLVideoElement>(null)
  useEffect(() => {
    if (started) ref.current?.play().catch(() => {})
  }, [started])
  useEffect(() => {
    if (!active && ref.current && !ref.current.paused) ref.current.pause()
  }, [active])
  if (!m.video) return null
  if (!started)
    return (
      <button type="button" className="vposter" onClick={() => setStarted(true)} aria-label="Смотреть видео">
        <span className="pl">
          <Play size={2} />
        </span>
        <span className="num">Видео{m.duration ? ` · ${formatDuration(m.duration)}` : ''}</span>
      </button>
    )
  return <video ref={ref} src={m.video} controls playsInline preload="metadata" />
}

/** Phones: full-width square slides with scroll-snap and dots. */
export function Gallery({ media, title, device, color }: Props) {
  const trackRef = useRef<HTMLDivElement>(null)
  const [index, setIndex] = useState(0)
  const [viewer, setViewer] = useState<number | null>(null)
  const photos = media.filter((m) => m.kind === 'photo')

  const onScroll = useCallback(() => {
    const el = trackRef.current
    if (!el) return
    const i = Math.round(el.scrollLeft / el.clientWidth)
    setIndex((p) => (p === i ? p : i))
  }, [])

  if (!media.length)
    return (
      <div className="gal">
        <div className="sl">
          <div className="fr">
            <Silhouette device={device} color={color} />
          </div>
        </div>
      </div>
    )

  return (
    <>
      <div className="gal hs" ref={trackRef} onScroll={onScroll} aria-roledescription="галерея">
        {media.map((m, i) => (
          <div className="sl" key={i} aria-label={`${i + 1} из ${media.length}`}>
            <div className="fr">
              {m.kind === 'video' ? (
                <VideoFrame m={m} active={i === index} />
              ) : (
                <button type="button" className="ph-btn" onClick={() => setViewer(photos.indexOf(m))} aria-label="Открыть фото целиком">
                  <ProductImage src={m.preview ?? m.full} alt={`${title}, фото ${i + 1}`} device={device} color={color} eager={i === 0} />
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
      {media.length > 1 && (
        <div className="dots" aria-hidden="true">
          {media.map((_, i) => (
            <i key={i} className={i === index ? 'on' : ''} />
          ))}
        </div>
      )}
      {viewer !== null && viewer >= 0 && <Viewer photos={photos} start={viewer} title={title} onClose={() => setViewer(null)} />}
    </>
  )
}

/** Desktop: one big frame + thumbnails. */
export function DeskGallery({ media, title, device, color }: Props) {
  const [cur, setCur] = useState(0)
  const [viewer, setViewer] = useState<number | null>(null)
  const photos = media.filter((m) => m.kind === 'photo')
  const m = media[cur]
  return (
    <div className="d-gal">
      <div className="big">
        {!m ? (
          <Silhouette device={device} color={color} />
        ) : m.kind === 'video' ? (
          <VideoFrame key={cur} m={m} active />
        ) : (
          <button type="button" className="ph-btn" onClick={() => setViewer(photos.indexOf(m))} aria-label="Открыть фото целиком">
            <ProductImage src={m.full ?? m.preview} alt={`${title}, фото ${cur + 1}`} device={device} color={color} eager />
          </button>
        )}
      </div>
      {media.length > 1 && (
        <div className="thumbs">
          {media.map((x, i) => (
            <button key={i} type="button" className={i === cur ? 'on' : ''} onClick={() => setCur(i)} aria-label={`${x.kind === 'video' ? 'Видео' : 'Фото'} ${i + 1}`}>
              {x.kind === 'video' ? <Play size={2} /> : <ProductImage src={x.preview ?? x.full} alt="" device={device} color={color} />}
            </button>
          ))}
        </div>
      )}
      {viewer !== null && viewer >= 0 && <Viewer photos={photos} start={viewer} title={title} onClose={() => setViewer(null)} />}
    </div>
  )
}

function Viewer({ photos, start, title, onClose }: { photos: MediaItem[]; start: number; title: string; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const [i, setI] = useState(start)
  useBackButton(onClose)
  useScrollLock()

  useEffect(() => {
    const el = ref.current
    if (el) el.scrollLeft = start * el.clientWidth
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') ref.current?.scrollBy({ left: (e.key === 'ArrowRight' ? 1 : -1) * ref.current.clientWidth, behavior: 'smooth' })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="viewer" role="dialog" aria-modal="true" aria-label={title}>
      <div className="track" ref={ref} onScroll={(e) => setI(Math.round(e.currentTarget.scrollLeft / e.currentTarget.clientWidth))}>
        {photos.map((p, k) => (
          <div key={k} onClick={onClose}>
            <img src={p.full ?? p.preview ?? ''} alt={`${title}, фото ${k + 1}`} loading={Math.abs(k - start) <= 1 ? 'eager' : 'lazy'} />
          </div>
        ))}
      </div>
      <div className="vbar">
        <span className="num">
          {i + 1} из {photos.length}
        </span>
        <button type="button" className="x-btn" onClick={onClose} aria-label="Закрыть">
          <X />
        </button>
      </div>
    </div>
  )
}
