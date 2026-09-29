import type { ReactNode } from 'react'

/** Line icons ported 1:1 from the mockup (1.5px stroke, SF Symbols-like). */
function Sv({ w, h, sw = 1.5, children, className }: { w: number; h: number; sw?: number; children: ReactNode; className?: string }) {
  return (
    <svg
      width={w}
      height={h}
      viewBox={`0 0 ${w} ${h}`}
      fill="none"
      stroke="currentColor"
      strokeWidth={sw}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
    >
      {children}
    </svg>
  )
}

export const ChevD = () => (
  <Sv w={10} h={10}>
    <path d="M1.5 3.5 5 7l3.5-3.5" />
  </Sv>
)
export const ChevD14 = () => (
  <Sv w={14} h={14} sw={1.6}>
    <path d="M2.5 5 7 9.5 11.5 5" />
  </Sv>
)
export const ChevR = () => (
  <Sv w={7} h={12}>
    <path d="M1.5 1.5 6 6l-4.5 4.5" />
  </Sv>
)
export const Back = () => (
  <Sv w={12} h={20} sw={2.2}>
    <path d="M10 2 2 10l8 8" />
  </Sv>
)
export const X = () => (
  <Sv w={12} h={12} sw={1.8}>
    <path d="M2 2l8 8M10 2l-8 8" />
  </Sv>
)
export const Xs = () => (
  <Sv w={8} h={8} sw={1.4}>
    <path d="M1.2 1.2l5.6 5.6M6.8 1.2 1.2 6.8" />
  </Sv>
)
export const Xl = () => (
  <Sv w={18} h={18}>
    <path d="M3 3l12 12M15 3 3 15" />
  </Sv>
)
export const Xp = () => (
  <Sv w={10} h={10} sw={1.6}>
    <path d="M2 2l6 6M8 2 2 8" />
  </Sv>
)
export const Search = () => (
  <Sv w={15} h={15}>
    <circle cx="6.5" cy="6.5" r="4.8" />
    <path d="M10 10l3.6 3.6" />
  </Sv>
)
export const Search17 = () => (
  <Sv w={17} h={17} sw={1.7}>
    <circle cx="7.3" cy="7.3" r="5.6" />
    <path d="M11.4 11.4l4.1 4.1" />
  </Sv>
)
export const Sliders = () => (
  <Sv w={16} h={16} sw={1.4}>
    <path d="M1.5 4.5h7.5M13.5 4.5h1M1.5 11.5h1M7 11.5h7.5" />
    <circle cx="11.2" cy="4.5" r="2.1" />
    <circle cx="4.8" cy="11.5" r="2.1" />
  </Sv>
)
export const Check = () => (
  <Sv w={14} h={14} sw={1.8}>
    <path d="M2.5 7.4 5.6 10.4 11.5 3.6" />
  </Sv>
)
export const CheckS = () => (
  <Sv w={12} h={12} sw={1.6}>
    <path d="M2 6.3 4.8 9 10 3" />
  </Sv>
)
export const Dash = () => (
  <Sv w={12} h={12}>
    <path d="M2.5 6h7" />
  </Sv>
)
export const Play = ({ size = 1 }: { size?: number }) => (
  <svg width={7 * size} height={8 * size} viewBox="0 0 7 8" aria-hidden="true">
    <path d="M0 0l7 4-7 4z" fill="currentColor" />
  </svg>
)
export const OkBig = () => (
  <Sv w={28} h={28} sw={2.6}>
    <path d="M6 14.5 11.5 20 22.5 8" />
  </Sv>
)
