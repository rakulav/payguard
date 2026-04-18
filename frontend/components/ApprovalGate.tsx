"use client";

interface ApprovalGateProps {
  data: {
    investigation_id?: string;
    recommendation?: string;
    verdict?: string;
    summary?: string;
  };
  onDecision: (decision: string) => void;
}

export default function ApprovalGate({ data, onDecision }: ApprovalGateProps) {
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
      <div className="bg-[#1a1a1a] border border-[#333] rounded-xl p-6 max-w-lg w-full mx-4 shadow-2xl">
        <div className="flex items-center gap-2 mb-4">
          <div className="w-10 h-10 bg-amber-900/50 rounded-full flex items-center justify-center text-xl">
            🔒
          </div>
          <div>
            <h3 className="text-lg font-semibold">Approval Required</h3>
            <p className="text-sm text-gray-400">
              Human-in-the-loop verification needed
            </p>
          </div>
        </div>

        <div className="bg-[#0d0d0d] rounded-lg p-4 mb-4 text-sm">
          <div className="flex justify-between mb-2">
            <span className="text-gray-500">Verdict</span>
            <span className="badge-fraud">
              {data.verdict?.toUpperCase() || "FRAUD"}
            </span>
          </div>
          <div className="flex justify-between mb-2">
            <span className="text-gray-500">Recommendation</span>
            <span className="font-semibold text-amber-400">
              {data.recommendation?.toUpperCase() || "FREEZE"}
            </span>
          </div>
          {data.summary && (
            <div className="mt-3 pt-3 border-t border-[#262626]">
              <p className="text-gray-400 text-xs whitespace-pre-wrap max-h-40 overflow-y-auto">
                {data.summary}
              </p>
            </div>
          )}
        </div>

        <p className="text-sm text-gray-400 mb-4">
          The agent recommends{" "}
          <strong className="text-amber-400">
            {data.recommendation || "freezing"}
          </strong>{" "}
          this account. This action requires human approval before execution.
        </p>

        <div className="flex gap-3">
          <button
            onClick={() => onDecision("approve")}
            className="btn-success flex-1"
          >
            ✓ Approve {data.recommendation || "Action"}
          </button>
          <button
            onClick={() => onDecision("reject")}
            className="btn-danger flex-1"
          >
            ✕ Reject
          </button>
        </div>
      </div>
    </div>
  );
}
