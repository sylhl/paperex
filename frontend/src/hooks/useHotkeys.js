/* 全局快捷键：绑定对象形如 { 'mod+o': fn, 'escape': fn, '?': fn } */

import { useEffect, useRef } from 'react'

const EDITABLE = 'input, textarea, select, [contenteditable="true"]'

function eventCombo(event) {
  if (event.isComposing) return ''
  const parts = []
  if (event.ctrlKey || event.metaKey) parts.push('mod')
  if (event.altKey) parts.push('alt')
  let key = String(event.key || '')
  if (!key) return ''
  if (['Control', 'Meta', 'Alt', 'Shift', 'CapsLock'].includes(key)) return ''
  if (key.length === 1) {
    key = key.toLowerCase()
    // 不同键盘布局下 Ctrl+ 加号可能是 '+' 或 '='，统一按 '=' 处理
    if (key === '+') key = '='
  } else {
    key = key.toLowerCase()
  }
  parts.push(key)
  return parts.join('+')
}

export function useHotkeys(bindings) {
  const bindingsRef = useRef(bindings)
  bindingsRef.current = bindings

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.defaultPrevented || event.isComposing) return
      const combo = eventCombo(event)
      if (!combo) return
      const map = bindingsRef.current || {}
      const handler = map[combo]
      if (!handler) return
      const target = event.target instanceof Element ? event.target : null
      const editing = Boolean(target && target.closest(EDITABLE))
      if (editing && combo !== 'escape') return
      event.preventDefault()
      handler(event)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])
}
