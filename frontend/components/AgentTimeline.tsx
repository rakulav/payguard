'use client'

interface AgentEvent {
  agent: string
  type: string
  content: any
}

const agentColors: Record<string, string> = {
  orchestrator: 'text-gray-400 border-gray-700',
  triage: 'text-amber-400 border-amber-800',
  behavior: 'text-purple-400 border-purple-800',
  synthesis: 'text-blue-400 border-blue-800',
  system: 'text-green-400 border-green-800',
}

const agentLabels: Record<string, string> = {
  orchestrator: 'Orchestrator',
  triage: 'Triage Agent',
  behavior: 'Behavior Agent',
  synthesis: 'Synthesis Agent',
  system: 'System',
}

const typeIcons: Record<string, string> = {
  thought: '💭',
  tool_call: '🔧',
  tool_result: '📊',
  verdict: '⚖️',
  approval_required: '🔒',
  error: '❌',
}

export default function AgentTimeline({ events }: { events: AgentEvent[] }) {
  return (
    <div className="space-y-2">
      {events.map((event, i) => {
        const colorClass = agentColors[event.agent] || 'text-gray-400 border-gray-700'
        const label = agentLabels[event.agent] || event.agent
        const icon = typeIcons[event.type] || '•'

        let content = event.content
        if (typeof content === 'object') {
          content = JSON.stringify(content, null, 2)
        }

        return (
          <div key={i} className={`border-l-2 pl-3 py-1 ${colorClass}`}>
            <div className="flex items-center gap-2 text-xs mb-1">
              <span>{icon}</span>
              <span className="font-medium">{label}</span>
              <span className="text-gray-600">{event.type}</span>
            </div>
            {event.type === 'verdict' ? (
              <pre className="text-xs bg-[#0d0d0d] rounded p-2 overflow-x-auto whitespace-pre-wrap">{content}</pre>
            ) : (
              <p className="text-sm text-gray-300">{content}</p>
            )}
          </div>
        )
      })}
      {events.length === 0 && (
        <div className="text-center text-gray-600 py-8">
          <div className="animate-spin inline-block w-6 h-6 border-2 border-blue-500 border-t-transparent rounded-full mb-2" />
          <p>Waiting for agent events...</p>
        </div>
      )}
    </div>
  )
}
