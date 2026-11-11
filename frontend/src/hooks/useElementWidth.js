/* 元素宽度测量：ResizeObserver 优先，降级 window.resize */

import { useEffect, useRef, useState } from 'react'

export function useElementWidth(initial = 0) {
  const ref = useRef(null)
  const [width, setWidth] = useState(initial)

  useEffect(() => {
    const node = ref.current
    if (!node) return undefined
    const update = () => setWidth(node.clientWidth)
    update()
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', update)
      return () => window.removeEventListener('resize', update)
    }
    const observer = new ResizeObserver(update)
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  return [ref, width]
}
