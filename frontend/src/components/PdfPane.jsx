/* 单侧 PDF 面板：Document 载入、页面窗口渲染、滚动测量 */

import { useCallback, useEffect } from 'react'
import { Document } from 'react-pdf'
import 'react-pdf/dist/Page/AnnotationLayer.css'
import 'react-pdf/dist/Page/TextLayer.css'
import { inRenderWindow } from '../hooks/usePdfSync.js'
import { useElementWidth } from '../hooks/useElementWidth.js'
import { Icon } from './Icon.jsx'
import { PageSlot } from './PageSlot.jsx'

export function PdfPane({ side, title, hint, icon, file, zoom, sync, onLoadError, onWidthChange }) {
  const [measureRef, width] = useElementWidth()
  const pageWidth = Math.max(220, Math.round((width - 32) * zoom))

  useEffect(() => {
    onWidthChange?.(width)
  }, [width, onWidthChange])

  const attach = useCallback(
    (node) => {
      measureRef.current = node
      sync.setScroller(side, node)
    },
    [measureRef, side, sync]
  )

  const pages = Array.from({ length: sync.numPages || 0 }, (_, index) => index + 1)

  return (
    <div className="pane" data-side={side}>
      <div className="pane-head">
        <span className="flex items-center gap-1.5 text-[11px] font-medium tracking-wide text-zinc-400">
          <Icon name={icon || (side === 'left' ? 'Eye' : 'Spark')} className="h-3.5 w-3.5" />
          {title}
        </span>
        <span className="hidden text-[10px] text-zinc-600 sm:inline">{hint}</span>
      </div>
      <div ref={attach} className="scroll-y pane-scroll" onScroll={() => sync.handleScroll(side)}>
        {!file ? (
          <div className="pane-fallback">
            <Icon name="Close" className="h-5 w-5" />
            <p>没有可用的文件地址</p>
          </div>
        ) : (
          <Document
            key={file}
            file={file}
            className="pdf-document"
            loading={
              <div className="pane-fallback">
                <span className="loading-spinner" />
                <p>正在载入 PDF…</p>
              </div>
            }
            error={
              <div className="pane-fallback">
                <Icon name="Close" className="h-5 w-5" />
                <p>PDF 载入失败</p>
              </div>
            }
            onLoadSuccess={(pdf) => {
              if (side === 'left' || sync.numPages === 0) sync.setDocumentPages(pdf.numPages)
            }}
            onLoadError={(error) => onLoadError?.(side, error)}
            onSourceError={(error) => onLoadError?.(side, error)}
          >
            <div className="pdf-canvas">
              {pages.map((page) => (
                <PageSlot
                  key={page}
                  page={page}
                  pageWidth={pageWidth}
                  pageHeight={Math.round(pageWidth * sync.ratioFor(page))}
                  render={inRenderWindow(page, sync.currentPage)}
                  nodeRef={sync.setPageNode(side, page)}
                  onPageRatio={sync.setPageRatio}
                />
              ))}
            </div>
          </Document>
        )}
      </div>
    </div>
  )
}
