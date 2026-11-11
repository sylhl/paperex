/* 快捷键帮助 */

import { SHORTCUTS } from '../constants.js'
import { Icon } from './Icon.jsx'

export function HelpDialog({ open, onClose }) {
  if (!open) return null
  return (
    <div className="help-overlay" onClick={onClose}>
      <div className="help-shell" role="dialog" aria-modal="true" aria-label="快捷键帮助" onClick={(event) => event.stopPropagation()}>
        <header className="help-head">
          <div className="flex items-center gap-2">
            <span className="qa-panel-icon">
              <Icon name="Help" className="h-4 w-4" />
            </span>
            <div>
              <h2 className="qa-panel-title">快捷键与使用提示</h2>
              <p className="qa-panel-sub">阅读器与全局操作</p>
            </div>
          </div>
          <button type="button" className="icon-btn h-7 w-7" onClick={onClose} aria-label="关闭帮助">
            <Icon name="Close" className="h-3.5 w-3.5" />
          </button>
        </header>
        <div className="help-body">
          <ul className="help-list">
            {SHORTCUTS.map((item) => (
              <li key={item.label}>
                <span className="help-keys">
                  {item.keys.map((key) => (
                    <kbd key={key}>{key}</kbd>
                  ))}
                </span>
                <span className="help-label">{item.label}</span>
              </li>
            ))}
          </ul>
          <div className="help-tips">
            <p>
              <strong>双栏阅读：</strong>左原文右译文，滚动自动同步；点「同步」可关闭。QA 报告里的页码可以直接跳到对应页。
            </p>
            <p>
              <strong>转换失败：</strong>错误卡会给出原因与建议动作，可复制详情；网络抖动不会中断轮询，会自动重试。
            </p>
            <p>
              <strong>本地后端：</strong>默认地址 http://127.0.0.1:8756，可在设置 → 高级里修改（保存后重启生效）。
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
