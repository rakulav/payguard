'use client'

import { useState, useEffect } from 'react'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

interface BenchmarkRow {
  scenario_id: string
  transaction_id: string
  ground_truth: string
  category?: string
  rules_verdict: string
  rules_confidence: string
  rules_latency_ms: string
  rules_fired?: string
  agent_verdict: string
  agent_confidence: string
  agent_latency_ms: string
  pattern_detected: string
}

interface BenchmarkSummary {
  scenarios_run?: number
  f1_lift_pct?: number
  median_agent_latency_ms?: number
  rules?: { precision: number; recall: number; f1: number }
  agent?: { precision: number; recall: number; f1: number }
}

function verdictMatchesGroundTruth(verdict: string, groundTruth: string): boolean {
  if (groundTruth === 'ambiguous') return verdict === 'suspicious'
  return verdict === groundTruth
}

export default function BenchmarksPage() {
  const [data, setData] = useState<BenchmarkRow[]>([])
  const [summary, setSummary] = useState<BenchmarkSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const res = await fetch(`${API_URL}/api/benchmarks/results`)
        if (!res.ok) {
          const body = await res.json().catch(() => ({}))
          throw new Error((body as { detail?: string }).detail || res.statusText)
        }
        const json = await res.json()
        if (cancelled) return
        setData(json.scenarios || [])
        setSummary(json.summary || null)
        setError(null)
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : 'Failed to load benchmarks')
          setData([])
          setSummary(null)
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const scenariosCount = summary?.scenarios_run ?? data.length
  const f1Lift = summary?.f1_lift_pct ?? 0
  const medianSeconds =
    summary?.median_agent_latency_ms != null
      ? summary.median_agent_latency_ms / 1000
      : (() => {
          const agentLatencies = data.map((r) => parseInt(r.agent_latency_ms, 10) || 0)
            .sort((a, b) => a - b)
          if (agentLatencies.length === 0) return 0
          return agentLatencies[Math.floor(agentLatencies.length / 2)] / 1000
        })()

  const rulesPrec = summary?.rules?.precision != null ? summary.rules.precision * 100 : 0
  const rulesRec = summary?.rules?.recall != null ? summary.rules.recall * 100 : 0
  const agentPrec = summary?.agent?.precision != null ? summary.agent.precision * 100 : 0
  const agentRec = summary?.agent?.recall != null ? summary.agent.recall * 100 : 0

  const chartData = [
    { name: 'Precision', Rules: rulesPrec, Agent: agentPrec },
    { name: 'Recall', Rules: rulesRec, Agent: agentRec },
  ]

  const patternChart = data.reduce(
    (acc, row) => {
      const pattern = row.pattern_detected || 'unknown'
      if (!acc[pattern]) acc[pattern] = { pattern, rules: 0, agent: 0 }
      if (verdictMatchesGroundTruth(row.rules_verdict, row.ground_truth)) acc[pattern].rules++
      if (verdictMatchesGroundTruth(row.agent_verdict, row.ground_truth)) acc[pattern].agent++
      return acc
    },
    {} as Record<string, { pattern: string; rules: number; agent: number }>
  )

  return (
    <div>
      <h1 className="text-2xl font-bold mb-2">Benchmark Results</h1>
      <p className="text-gray-500 text-sm mb-6">
        Comparison of rules-only baseline vs. multi-agent pipeline across {scenariosCount || '—'} fraud scenarios
      </p>

      {error && (
        <div className="card mb-4 border border-amber-900/50 text-amber-200 text-sm">
          Could not load results: {error}. Ensure the API is running and benchmarks have been executed.
        </div>
      )}

      {/* Key metrics */}
      <div className="grid grid-cols-3 gap-4 mb-6">
        <div className="card text-center">
          <div className="text-3xl font-bold text-blue-400">+{f1Lift.toFixed(0)}%</div>
          <div className="text-sm text-gray-400 mt-1">F1 lift over rules</div>
        </div>
        <div className="card text-center">
          <div className="text-3xl font-bold text-green-400">{medianSeconds.toFixed(1)}s</div>
          <div className="text-sm text-gray-400 mt-1">Median investigation latency</div>
        </div>
        <div className="card text-center">
          <div className="text-3xl font-bold text-purple-400">{scenariosCount}</div>
          <div className="text-sm text-gray-400 mt-1">Test scenarios</div>
        </div>
      </div>

      {/* Charts */}
      <div className="grid grid-cols-2 gap-4 mb-6">
        <div className="card">
          <h3 className="text-sm font-medium text-gray-400 mb-4">Agent vs Rules: Precision &amp; recall (%)</h3>
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#262626" />
              <XAxis dataKey="name" stroke="#666" />
              <YAxis stroke="#666" domain={[0, 100]} />
              <Tooltip contentStyle={{ background: '#1a1a1a', border: '1px solid #333' }} />
              <Legend />
              <Bar dataKey="Rules" fill="#f59e0b" />
              <Bar dataKey="Agent" fill="#3b82f6" />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="card">
          <h3 className="text-sm font-medium text-gray-400 mb-4">Correct detections by pattern</h3>
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={Object.values(patternChart)}>
              <CartesianGrid strokeDasharray="3 3" stroke="#262626" />
              <XAxis dataKey="pattern" stroke="#666" tick={{ fontSize: 10 }} />
              <YAxis stroke="#666" allowDecimals={false} />
              <Tooltip contentStyle={{ background: '#1a1a1a', border: '1px solid #333' }} />
              <Legend />
              <Bar dataKey="rules" fill="#f59e0b" name="Rules" />
              <Bar dataKey="agent" fill="#3b82f6" name="Agent" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Results table */}
      <div className="card overflow-hidden">
        <h3 className="text-sm font-medium text-gray-400 mb-3">Detailed Results (comparison.csv)</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-[#262626] text-left text-gray-500">
                <th className="px-3 py-2">Scenario</th>
                <th className="px-3 py-2">Transaction</th>
                <th className="px-3 py-2">Truth</th>
                <th className="px-3 py-2">Rules</th>
                <th className="px-3 py-2 text-right">Rules ms</th>
                <th className="px-3 py-2">Agent</th>
                <th className="px-3 py-2 text-right">Agent ms</th>
                <th className="px-3 py-2">Pattern</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={8} className="text-center py-4 text-gray-600">
                    Loading...
                  </td>
                </tr>
              ) : data.length === 0 ? (
                <tr>
                  <td colSpan={8} className="text-center py-4 text-gray-600">
                    No scenario rows returned.
                  </td>
                </tr>
              ) : (
                data.map((row) => (
                  <tr key={row.scenario_id} className="border-b border-[#1a1a1a] hover:bg-[#1a1a1a]">
                    <td className="px-3 py-2 font-mono">{row.scenario_id}</td>
                    <td className="px-3 py-2 font-mono">{row.transaction_id}</td>
                    <td className="px-3 py-2">
                      <span className="badge-fraud">{row.ground_truth}</span>
                    </td>
                    <td className="px-3 py-2">
                      <span
                        className={
                          verdictMatchesGroundTruth(row.rules_verdict, row.ground_truth)
                            ? 'text-green-400'
                            : 'text-red-400'
                        }
                      >
                        {row.rules_verdict}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right font-mono">{row.rules_latency_ms}</td>
                    <td className="px-3 py-2">
                      <span
                        className={
                          verdictMatchesGroundTruth(row.agent_verdict, row.ground_truth)
                            ? 'text-green-400'
                            : 'text-red-400'
                        }
                      >
                        {row.agent_verdict}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right font-mono">{row.agent_latency_ms}</td>
                    <td className="px-3 py-2 text-gray-500">{row.pattern_detected}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
