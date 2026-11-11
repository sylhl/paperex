/* 双栏 PDF 同步滚动：页面锚点测量、程序化滚动锁、页码跳转、版面重锚 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { DEFAULT_PAGE_RATIO, PAGE_RENDER_WINDOW, STORAGE_KEYS } from '../constants.js'
import { clamp, readStored, writeStored } from '../lib/storage.js'

const SYNC_DEBOUNCE = 60
const PROGRAM_LOCK = 180

/* 页面横向渲染窗口：只渲染 currentPage ± PAGE_RENDER_WINDOW */
export function inRenderWindow(page, currentPage) {
  return Math.abs(page - currentPage) <= PAGE_RENDER_WINDOW
}

export function usePdfSync() {
  const [numPages, setNumPages] = useState(0)
  const [currentPage, setCurrentPage] = useState(1)
  const [pageDraft, setPageDraft] = useState('1')
  const [syncEnabled, setSyncEnabled] = useState(() => readStored(STORAGE_KEYS.sync, true) !== false)
  const [pageRatios, setPageRatios] = useState({})

  const baseRatioRef = useRef(DEFAULT_PAGE_RATIO)
  const scrollersRef = useRef({ left: null, right: null })
  const nodesRef = useRef({ left: new Map(), right: new Map() })
  const lockRef = useRef({ left: 0, right: 0 })
  const syncedRef = useRef(syncEnabled)
  syncedRef.current = syncEnabled
  const numPagesRef = useRef(0)
  numPagesRef.current = numPages
  const currentPageRef = useRef(1)
  const activeSideRef = useRef('left')
  const lastRatioRef = useRef(0)
  const syncTimerRef = useRef(null)
  const rafRef = useRef(0)
  const pendingSideRef = useRef('')
  const pageRatiosRef = useRef({})
  pageRatiosRef.current = pageRatios

  const setScroller = useCallback((side, node) => {
    scrollersRef.current[side] = node
  }, [])

  const setPageNode = useCallback(
    (side, page) => (node) => {
      const map = nodesRef.current[side]
      if (!map) return
      if (node) map.set(page, node)
      else map.delete(page)
    },
    []
  )

  /* 测量某一侧所有已挂载页面的位置（相对滚动容器内容坐标） */
  const measure = useCallback((side) => {
    const scroller = scrollersRef.current[side]
    if (!scroller) return []
    const map = nodesRef.current[side]
    if (!map || map.size === 0) return []
    const scrollerTop = scroller.getBoundingClientRect().top - scroller.scrollTop
    const list = []
    map.forEach((node, page) => {
      if (!node || !node.isConnected) return
      const rect = node.getBoundingClientRect()
      if (rect.height <= 0) return
      list.push({ page, top: rect.top - scrollerTop, height: rect.height })
    })
    list.sort((a, b) => a.page - b.page)
    return list
  }, [])

  const scrollStateFrom = useCallback(
    (side) => {
      const scroller = scrollersRef.current[side]
      const metrics = measure(side)
      if (!scroller || metrics.length === 0) return null
      const scrollTop = scroller.scrollTop
      let page = metrics[0].page
      let ratio = 0
      for (const metric of metrics) {
        if (scrollTop + 1 >= metric.top) {
          page = metric.page
          ratio = clamp((scrollTop - metric.top) / metric.height, 0, 1)
        } else {
          break
        }
      }
      return { page, ratio }
    },
    [measure]
  )

  const scrollSideTo = useCallback(
    (side, page, ratio) => {
      const scroller = scrollersRef.current[side]
      const metrics = measure(side)
      if (!scroller || metrics.length === 0) return
      let target = metrics.find((metric) => metric.page === page)
      if (!target) {
        // 目标页不在已挂载集合里：退回到最接近的一页
        target = metrics.reduce(
          (best, metric) => (!best || Math.abs(metric.page - page) < Math.abs(best.page - page) ? metric : best),
          null
        )
      }
      if (!target) return
      const top = Math.round(target.top + clamp(ratio, 0, 1) * target.height)
      lockRef.current[side] = Date.now() + PROGRAM_LOCK
      scroller.scrollTo({ top: Math.max(0, top), behavior: 'auto' })
    },
    [measure]
  )

  const gotoPage = useCallback(
    (input, ratio = 0) => {
      const max = Math.max(1, numPagesRef.current)
      const page = clamp(Math.round(Number(input) || 1), 1, max)
      currentPageRef.current = page
      lastRatioRef.current = clamp(ratio, 0, 1)
      setCurrentPage(page)
      setPageDraft(String(page))
      requestAnimationFrame(() => {
        scrollSideTo('left', page, lastRatioRef.current)
        scrollSideTo('right', page, lastRatioRef.current)
      })
    },
    [scrollSideTo]
  )

  /* 版面变化（zoom/分栏宽度）后重新对齐到当前锚点 */
  const reapplyAnchor = useCallback(() => {
    const page = currentPageRef.current
    const ratio = lastRatioRef.current
    requestAnimationFrame(() => {
      scrollSideTo('left', page, ratio)
      scrollSideTo('right', page, ratio)
    })
  }, [scrollSideTo])

  const handleScroll = useCallback(
    (side) => {
      if (Date.now() < lockRef.current[side]) return
      pendingSideRef.current = side
      if (rafRef.current) return
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = 0
        const active = pendingSideRef.current
        const state = scrollStateFrom(active)
        if (!state) return
        activeSideRef.current = active
        lastRatioRef.current = state.ratio
        if (state.page !== currentPageRef.current) {
          currentPageRef.current = state.page
          setCurrentPage(state.page)
          setPageDraft(String(state.page))
        }
        if (!syncedRef.current) return
        clearTimeout(syncTimerRef.current)
        syncTimerRef.current = setTimeout(() => {
          const other = active === 'left' ? 'right' : 'left'
          scrollSideTo(other, state.page, state.ratio)
        }, SYNC_DEBOUNCE)
      })
    },
    [scrollStateFrom, scrollSideTo]
  )

  const setDocumentPages = useCallback((count) => {
    const pages = Math.max(0, Number(count) || 0)
    if (pages === numPagesRef.current) return
    numPagesRef.current = pages
    setNumPages(pages)
    currentPageRef.current = 1
    lastRatioRef.current = 0
    setCurrentPage(1)
    setPageDraft('1')
    // 清除超出页数范围的旧节点
    ;['left', 'right'].forEach((side) => {
      const scroller = scrollersRef.current[side]
      if (scroller) scroller.scrollTop = 0
      const map = nodesRef.current[side]
      if (!map) return
      Array.from(map.keys()).forEach((page) => {
        if (page > pages) map.delete(page)
      })
    })
  }, [])

  const setPageRatio = useCallback((page, ratio) => {
    const value = Number(ratio)
    if (!Number.isFinite(value) || value < 0.2 || value > 4) return
    if (page === 1) baseRatioRef.current = value
    setPageRatios((prev) => {
      if (Math.abs((prev[page] || 0) - value) < 0.001) return prev
      return { ...prev, [page]: value }
    })
  }, [])

  const ratioFor = useCallback(
    (page) => {
      const known = pageRatiosRef.current[page]
      return known || baseRatioRef.current
    },
    []
  )

  const toggleSync = useCallback(() => {
    setSyncEnabled((prev) => {
      const next = !prev
      writeStored(STORAGE_KEYS.sync, next)
      return next
    })
  }, [])

  const commitPage = useCallback(() => {
    const parsed = Number(pageDraft)
    if (!Number.isFinite(parsed) || parsed < 1) {
      setPageDraft(String(currentPageRef.current))
      return
    }
    gotoPage(parsed)
  }, [pageDraft, gotoPage])

  useEffect(
    () => () => {
      clearTimeout(syncTimerRef.current)
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
    },
    []
  )

  return {
    numPages,
    currentPage,
    pageDraft,
    setPageDraft,
    syncEnabled,
    toggleSync,
    gotoPage,
    commitPage,
    handleScroll,
    setScroller,
    setPageNode,
    setDocumentPages,
    setPageRatio,
    ratioFor,
    reapplyAnchor,
    currentPageRef
  }
}
