/* 左侧边栏：品牌、主动作、导航、最近译文、在线状态与主题切换 */

import { APP_VERSION, THEMES } from '../constants.js'
import { formatDate, truncateMiddle } from '../lib/format.js'
import { Icon } from './Icon.jsx'

export function Sidebar({
  mode,
  theme,
  historyCount,
  recent = [],
  currentJobId,
  backendOnline,
  busy,
  onNew,
  onHome,
  onSettings,
  onHelp,
  onToggleMode,
  onOpenRecent
}) {
  const themeName = THEMES.find((item) => item.id === theme)?.name || '薄荷'
  return (
    <aside className="app-sidebar">
      <div className="sidebar-brand">
        <span className="brand-mark">P</span>
        <div className="sidebar-brand-text">
          <strong>paperex</strong>
          <span>v{APP_VERSION}</span>
        </div>
      </div>

      <button type="button" className="side-new" onClick={onNew} disabled={busy}>
        <Icon name="Plus" className="h-3.5 w-3.5" />
        <span>新增翻译</span>
        <kbd className="side-kbd">Ctrl O</kbd>
      </button>

      <nav className="side-nav">
        <button type="button" className="side-item side-item-active" onClick={onHome} title="译文库">
          <Icon name="Grid" className="side-icon" />
          <span className="side-text">译文库</span>
          <span className="side-count">{historyCount}</span>
        </button>
        <button type="button" className="side-item" onClick={onSettings} title="设置">
          <Icon name="Gear" className="side-icon" />
          <span className="side-text">设置</span>
          <kbd className="side-kbd">Ctrl ,</kbd>
        </button>
        <button type="button" className="side-item" onClick={onHelp} title="快捷键">
          <Icon name="Help" className="side-icon" />
          <span className="side-text">快捷键</span>
          <span className="side-count">?</span>
        </button>
      </nav>

      <div className="side-recent">
        <p className="side-label">最近译文</p>
        {recent.length === 0 ? (
          <p className="side-empty">还没有翻译记录</p>
        ) : (
          <ul className="side-recent-list">
            {recent.slice(0, 8).map((item) => (
              <li key={item.jobId}>
                <button
                  type="button"
                  className={`side-recent-item ${item.jobId === currentJobId ? 'side-recent-item-active' : ''}`}
                  onClick={() => onOpenRecent(item)}
                  title={item.name}
                >
                  <span className={`side-recent-dot ${item.failed ? 'bg-rose-400' : item.qa?.skipped ? 'bg-zinc-500' : item.qa?.errors ? 'bg-rose-400' : item.qa?.warnings ? 'bg-amber-400' : 'bg-mint-400'}`} />
                  <span className="side-recent-name">{truncateMiddle(item.name, 26)}</span>
                  <span className="side-recent-date">{formatDate(item.createdAt).slice(5, 10)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="side-foot">
        <div className="side-status" title={backendOnline ? '本地后端在线' : '本地后端离线'}>
          <span className={`side-status-dot ${backendOnline ? 'side-status-on' : 'side-status-off'}`} />
          <span className="side-text">{backendOnline ? '后端在线' : '后端离线'}</span>
        </div>
        <button type="button" className="side-mode" onClick={onToggleMode} title={`切换到${mode === 'dark' ? '浅色' : '深色'}（${themeName}主题）`}>
          <Icon name={mode === 'dark' ? 'Moon' : 'Sun'} className="h-3.5 w-3.5" />
          <span className="side-text">{mode === 'dark' ? '深色' : '浅色'}</span>
        </button>
      </div>
    </aside>
  )
}
