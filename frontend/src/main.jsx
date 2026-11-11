import React from 'react'
import { createRoot } from 'react-dom/client'
import { pdfjs } from 'react-pdf'
import './index.css'
import './styles-v11.css'
import App from './App.jsx'
import { ErrorBoundary } from './components/ErrorBoundary.jsx'

/* pdf.js worker：交给 Vite 打包，桌面端与浏览器开发模式都能用 */
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url
).toString()

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>
)
