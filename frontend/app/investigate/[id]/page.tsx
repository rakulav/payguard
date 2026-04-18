"use client";

import { useState, useEffect, useRef } from "react";
import { useParams, useSearchParams } from "next/navigation";
import AgentTimeline from "@/components/AgentTimeline";
import ApprovalGate from "@/components/ApprovalGate";
import EvidencePanel from "@/components/EvidencePanel";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface AgentEvent {
  agent: string;
  type: string;
  content: any;
}

interface TransactionDetail {
  transaction_id: string;
  type: string;
  amount: number;
  name_orig: string;
  name_dest: string;
  old_balance_org: number;
  new_balance_orig: number;
  is_fraud: boolean;
  timestamp: string;
  merchant_category: string;
  country_code: string;
  ip_address: string;
  device_fingerprint: string;
}

export default function InvestigatePage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const investigationId = params.id as string;
  const txnId = searchParams.get("txn") || "";

  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [transaction, setTransaction] = useState<TransactionDetail | null>(
    null,
  );
  const [done, setDone] = useState(false);
  const [showApproval, setShowApproval] = useState(false);
  const [approvalData, setApprovalData] = useState<any>(null);
  const [similarFrauds, setSimilarFrauds] = useState<any[]>([]);
  const [auditEntries, setAuditEntries] = useState<any[]>([]);
  const [invMeta, setInvMeta] = useState<{
    cost_usd?: number;
    model_breakdown?: { agent: string; model: string }[];
    confidence?: number;
  } | null>(null);
  const [panelTab, setPanelTab] = useState<"live" | "audit">("live");
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (txnId) {
      fetch(`${API_URL}/api/transactions/${txnId}`)
        .then((r) => r.json())
        .then(setTransaction)
        .catch(() => undefined);
    }
  }, [txnId]);

  useEffect(() => {
    const es = new EventSource(`${API_URL}/api/stream/${investigationId}`);
    eventSourceRef.current = es;

    es.addEventListener("thought", (e) => {
      const data = JSON.parse(e.data);
      setEvents((prev) => [...prev, data]);
    });

    es.addEventListener("tool_call", (e) => {
      const data = JSON.parse(e.data);
      setEvents((prev) => [...prev, data]);
    });

    es.addEventListener("tool_result", (e) => {
      const data = JSON.parse(e.data);
      setEvents((prev) => [...prev, data]);
      if (
        data.content &&
        typeof data.content === "string" &&
        data.content.includes("similar")
      ) {
        try {
          const parsed = JSON.parse(data.content);
          if (parsed.similar_transactions)
            setSimilarFrauds(parsed.similar_transactions);
        } catch {}
      }
    });

    es.addEventListener("verdict", (e) => {
      const data = JSON.parse(e.data);
      setEvents((prev) => [...prev, data]);
    });

    es.addEventListener("approval_required", (e) => {
      const data = JSON.parse(e.data);
      setShowApproval(true);
      setApprovalData(data.content || data);
    });

    es.addEventListener("done", () => {
      setDone(true);
      es.close();
      fetch(`${API_URL}/api/investigations/${investigationId}`)
        .then((r) => r.json())
        .then((d) => {
          setInvMeta({
            cost_usd: d.cost_usd,
            model_breakdown: d.model_breakdown,
            confidence: d.confidence,
          });
        })
        .catch(() => {});
      fetch(`${API_URL}/api/investigations/${investigationId}/audit`)
        .then((r) => r.json())
        .then((d) => setAuditEntries(d.entries || []))
        .catch(() => {});
    });

    es.onerror = () => {
      setTimeout(() => setDone(true), 2000);
    };

    return () => {
      es.close();
    };
  }, [investigationId]);

  const handleApproval = async (decision: string) => {
    await fetch(`${API_URL}/api/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        investigation_id: investigationId,
        decision,
        approved_by: "demo_user",
      }),
    });
    setShowApproval(false);
    setEvents((prev) => [
      ...prev,
      {
        agent: "system",
        type: "thought",
        content: `Approval decision: ${decision.toUpperCase()}`,
      },
    ]);
  };

  return (
    <div className="space-y-3">
      {invMeta?.cost_usd != null && (
        <div className="text-sm text-gray-400 px-1">
          Investigation cost:{" "}
          <span className="font-mono text-emerald-300">
            ${Number(invMeta.cost_usd).toFixed(5)}
          </span>
          {invMeta.model_breakdown?.length ? (
            <span className="ml-3">
              (
              {invMeta.model_breakdown
                .map((m) => `${m.agent}:${m.model}`)
                .join(" · ")}
              )
            </span>
          ) : null}
        </div>
      )}
      <div className="flex gap-2 border-b border-gray-800 pb-2">
        <button
          type="button"
          className={`px-3 py-1 rounded text-sm ${panelTab === "live" ? "bg-gray-800 text-white" : "text-gray-500"}`}
          onClick={() => setPanelTab("live")}
        >
          Live trace
        </button>
        <button
          type="button"
          className={`px-3 py-1 rounded text-sm ${panelTab === "audit" ? "bg-gray-800 text-white" : "text-gray-500"}`}
          onClick={() => setPanelTab("audit")}
        >
          Audit trail
        </button>
      </div>
      <div className="grid grid-cols-12 gap-4 h-[calc(100vh-160px)]">
        {/* Left: Transaction Details */}
        <div className="col-span-3 card overflow-y-auto">
          <h2 className="text-lg font-semibold mb-4">Transaction Details</h2>
          {transaction ? (
            <div className="space-y-3 text-sm">
              <div>
                <span className="text-gray-500">ID</span>
                <p className="font-mono">{transaction.transaction_id}</p>
              </div>
              <div>
                <span className="text-gray-500">Type</span>
                <p>
                  <span className="px-2 py-0.5 rounded bg-[#1e1e1e] text-xs">
                    {transaction.type}
                  </span>
                </p>
              </div>
              <div>
                <span className="text-gray-500">Amount</span>
                <p className="text-lg font-bold">
                  $
                  {transaction.amount.toLocaleString(undefined, {
                    minimumFractionDigits: 2,
                  })}
                </p>
              </div>
              <div>
                <span className="text-gray-500">From</span>
                <p>{transaction.name_orig}</p>
              </div>
              <div>
                <span className="text-gray-500">To</span>
                <p>{transaction.name_dest}</p>
              </div>
              <div>
                <span className="text-gray-500">Balance Change</span>
                <p className="font-mono text-xs">
                  ${transaction.old_balance_org.toLocaleString()} → $
                  {transaction.new_balance_orig.toLocaleString()}
                </p>
              </div>
              <div>
                <span className="text-gray-500">Country</span>
                <p>{transaction.country_code}</p>
              </div>
              <div>
                <span className="text-gray-500">Category</span>
                <p>{transaction.merchant_category}</p>
              </div>
              <div>
                <span className="text-gray-500">Device</span>
                <p className="font-mono text-xs truncate">
                  {transaction.device_fingerprint}
                </p>
              </div>
              <div>
                <span className="text-gray-500">Status</span>
                <p>
                  {transaction.is_fraud ? (
                    <span className="badge-fraud">Fraud</span>
                  ) : (
                    <span className="badge-legit">Clean</span>
                  )}
                </p>
              </div>
            </div>
          ) : (
            <p className="text-gray-500">Loading...</p>
          )}
        </div>

        {/* Center: Agent Timeline or Audit */}
        <div className="col-span-6 card overflow-y-auto">
          {panelTab === "audit" ? (
            <>
              <h2 className="text-lg font-semibold mb-4">Audit trail</h2>
              {auditEntries.length === 0 ? (
                <p className="text-gray-500 text-sm">
                  No audit rows yet. Wait for the investigation to finish.
                </p>
              ) : (
                <ul className="space-y-3 text-sm">
                  {auditEntries.map((e) => (
                    <li key={e.id} className="border-l-2 border-gray-700 pl-3">
                      <div className="text-gray-500 text-xs">{e.timestamp}</div>
                      <div>
                        <span className="text-violet-300">{e.actor}</span>
                        <span className="text-gray-500"> → </span>
                        <span className="text-white font-medium">
                          {e.action}
                        </span>
                      </div>
                      {e.reason ? (
                        <p className="text-gray-500 text-xs mt-1">{e.reason}</p>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </>
          ) : (
            <>
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold">Agent Investigation</h2>
                {done ? (
                  <span className="badge bg-green-900/50 text-green-300 border border-green-800">
                    Complete
                  </span>
                ) : (
                  <span className="badge bg-blue-900/50 text-blue-300 border border-blue-800 animate-pulse">
                    Running...
                  </span>
                )}
              </div>
              <AgentTimeline events={events} />
              {showApproval && approvalData && (
                <ApprovalGate data={approvalData} onDecision={handleApproval} />
              )}
            </>
          )}
        </div>

        {/* Right: Evidence Panel */}
        <div className="col-span-3 card overflow-y-auto">
          <EvidencePanel events={events} similarFrauds={similarFrauds} />
        </div>
      </div>
    </div>
  );
}
