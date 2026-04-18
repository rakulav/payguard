'use client'

interface AgentEvent {
  agent: string
  type: string
  content: any
}

export default function EvidencePanel({
  events,
  similarFrauds,
}: {
  events: AgentEvent[]
  similarFrauds: any[]
}) {
  const verdictEvents = events.filter(e => e.type === 'verdict')
  const toolResults = events.filter(e => e.type === 'tool_result')

  return (
    <div>
      <h2 className="text-lg font-semibold mb-4">Evidence & Similar Cases</h2>

      {/* Verdicts */}
      {verdictEvents.length > 0 && (
        <div className="mb-4">
          <h3 className="text-sm font-medium text-gray-400 mb-2">Agent Verdicts</h3>
          {verdictEvents.map((event, i) => {
            let content = event.content
            if (typeof content === 'string') {
              try { content = JSON.parse(content) } catch {}
            }
            return (
              <div key={i} className="bg-[#0d0d0d] rounded p-3 mb-2 text-xs">
                <div className="font-medium text-gray-300 mb-1">{event.agent}</div>
                {typeof content === 'object' ? (
                  <div className="space-y-1">
                    {content.verdict && (
                      <div className="flex justify-between">
                        <span className="text-gray-500">Verdict</span>
                        <span className={content.verdict === 'fraud' || content.verdict === 'likely_fraud' ? 'text-red-400' : 'text-green-400'}>
                          {content.verdict}
                        </span>
                      </div>
                    )}
                    {content.confidence !== undefined && (
                      <div className="flex justify-between">
                        <span className="text-gray-500">Confidence</span>
                        <span>{(content.confidence * 100).toFixed(0)}%</span>
                      </div>
                    )}
                    {content.anomaly_score !== undefined && (
                      <div className="flex justify-between">
                        <span className="text-gray-500">Anomaly Score</span>
                        <span className="text-amber-400">{(content.anomaly_score * 100).toFixed(0)}%</span>
                      </div>
                    )}
                    {content.behavioral_flags && (
                      <div className="mt-2">
                        <span className="text-gray-500">Flags:</span>
                        <div className="flex flex-wrap gap-1 mt-1">
                          {content.behavioral_flags.map((flag: string, j: number) => (
                            <span key={j} className="px-1.5 py-0.5 bg-amber-900/30 text-amber-300 rounded text-[10px]">{flag}</span>
                          ))}
                        </div>
                      </div>
                    )}
                    {content.recommendation && (
                      <div className="flex justify-between mt-1">
                        <span className="text-gray-500">Recommendation</span>
                        <span className="font-medium text-amber-400">{content.recommendation}</span>
                      </div>
                    )}
                  </div>
                ) : (
                  <p className="text-gray-400">{String(content)}</p>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* Tool Results */}
      {toolResults.length > 0 && (
        <div className="mb-4">
          <h3 className="text-sm font-medium text-gray-400 mb-2">Tool Results ({toolResults.length})</h3>
          {toolResults.map((event, i) => (
            <div key={i} className="bg-[#0d0d0d] rounded p-2 mb-1 text-xs text-gray-400">
              <span className="text-gray-600">[{event.agent}]</span> {typeof event.content === 'string' ? event.content : JSON.stringify(event.content)}
            </div>
          ))}
        </div>
      )}

      {/* Similar Frauds */}
      {similarFrauds.length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-gray-400 mb-2">Similar Fraud Cases</h3>
          {similarFrauds.slice(0, 5).map((fraud, i) => (
            <div key={i} className="bg-[#0d0d0d] rounded p-2 mb-1 text-xs">
              <div className="flex justify-between">
                <span className="font-mono">{fraud.transaction_id}</span>
                <span className={fraud.is_fraud ? 'text-red-400' : 'text-green-400'}>
                  {fraud.is_fraud ? 'Fraud' : 'Clean'}
                </span>
              </div>
              <div className="text-gray-500 mt-0.5">
                {fraud.type} · ${fraud.amount?.toLocaleString()} · {fraud.country_code}
              </div>
              {fraud.sources && (
                <div className="text-[10px] text-gray-600 mt-0.5">
                  Sources: {fraud.sources.join(', ')}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {events.length === 0 && (
        <p className="text-gray-600 text-sm text-center py-4">
          Evidence will appear as agents complete their analysis...
        </p>
      )}
    </div>
  )
}
