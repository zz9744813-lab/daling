import React from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'

interface ErrorBoundaryState {
  hasError: boolean
  error?: Error
}

interface ErrorBoundaryProps {
  children: React.ReactNode
}

/**
 * 全局错误边界：捕获子组件渲染异常，显示友好提示
 */
export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // 骨架阶段：仅控制台输出
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  handleReset = () => {
    this.setState({ hasError: false, error: undefined })
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex h-full flex-col items-center justify-center px-6 text-center">
          <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-full bg-red-600/15 text-red-400">
            <AlertTriangle size={26} />
          </div>
          <h2 className="text-base font-medium text-gray-200">页面出现异常</h2>
          <p className="mt-2 max-w-md text-sm text-gray-500">
            {this.state.error?.message || '渲染过程中发生未知错误。'}
          </p>
          <button
            onClick={this.handleReset}
            className="mt-5 inline-flex items-center gap-1.5 rounded-md bg-ink-700 px-4 py-2 text-sm text-gray-200 hover:bg-ink-600"
          >
            <RefreshCw size={14} />
            重试
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
