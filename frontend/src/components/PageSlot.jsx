/* 单页占位槽：未进入渲染窗口时显示骨架，进入后渲染 react-pdf 页面 */

import { memo } from 'react'
import { Page } from 'react-pdf'

export const PageSlot = memo(function PageSlot({
  page,
  pageWidth,
  pageHeight,
  render,
  nodeRef,
  onPageRatio
}) {
  const skeleton = (
    <div className="page-skeleton" style={{ width: pageWidth, height: pageHeight }}>
      <span>第 {page} 页</span>
    </div>
  )

  return (
    <div ref={nodeRef} className="pdf-page-slot" style={{ width: pageWidth, height: pageHeight }}>
      {render ? (
        <Page
          pageNumber={page}
          width={pageWidth}
          loading={skeleton}
          error={<div className="page-skeleton page-skeleton-error" style={{ width: pageWidth, height: pageHeight }}><span>第 {page} 页载入失败</span></div>}
          onLoadSuccess={(pdfPage) => {
            try {
              const viewport = pdfPage.getViewport({ scale: 1 })
              if (viewport && viewport.width > 0) onPageRatio?.(page, viewport.height / viewport.width)
            } catch {
              /* 忽略页面尺寸探测失败 */
            }
          }}
        />
      ) : (
        skeleton
      )}
    </div>
  )
})
