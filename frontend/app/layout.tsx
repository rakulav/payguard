import type { Metadata } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: 'PayGuard - Fraud Investigation Assistant',
  description: 'LLM-Powered Payment Fraud Investigation Console',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen">
        <nav className="border-b border-[#262626] px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center text-white font-bold text-sm">PG</div>
            <span className="text-lg font-semibold">PayGuard</span>
            <span className="text-xs text-gray-500 ml-2">Fraud Investigation Assistant</span>
          </div>
          <div className="flex gap-4 text-sm">
            <a href="/" className="text-gray-400 hover:text-white transition-colors">Dashboard</a>
            <a href="/benchmarks" className="text-gray-400 hover:text-white transition-colors">Benchmarks</a>
          </div>
        </nav>
        <main className="p-6">{children}</main>
      </body>
    </html>
  )
}
