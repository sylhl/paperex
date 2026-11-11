/* React 错误边界：白屏兜底，可重载 / 清除上次记录 */

import { Component } from 'react'

export class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    // 保留控制台信息便于排查
    console.error('[paperex] 渲染异常：', error, info)
  }

  handleReload = () => {
    window.location.reload()
  }

  handleClear = () => {
    try {
      localStorage.removeItem('paperex.lastDoc')
    } catch {
      /* 忽略存储异常 */
    }
    window.location.reload()
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div className="error-screen">
        <div className="error-screen-card">
          <div className="error-screen-mark">!</div>
          <h1>界面出现了未预期的错误</h1>
          <p>这不是你的操作问题。可以先重载界面，或者清除上次打开的文档记录再试。</p>
          <pre>{String(error?.stack || error?.message || error)}</pre>
          <div className="error-screen-actions">
            <button type="button" className="btn btn-primary" onClick={this.handleReload}>
              重载界面
            </button>
            <button type="button" className="btn" onClick={this.handleClear}>
              清除上次记录并重载
            </button>
          </div>
        </div>
      </div>
    )
  }
}
