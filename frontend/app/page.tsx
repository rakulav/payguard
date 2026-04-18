"use client";

import { useState, useEffect, useCallback } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface Transaction {
  transaction_id: string;
  type: string;
  amount: number;
  name_orig: string;
  name_dest: string;
  is_fraud: boolean;
  is_flagged_fraud: boolean;
  timestamp: string;
  merchant_category: string;
  country_code: string;
}

export default function Dashboard() {
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [fraudOnly, setFraudOnly] = useState(false);
  const [loading, setLoading] = useState(true);
  const [investigating, setInvestigating] = useState<string | null>(null);
  const limit = 25;

  const fetchTransactions = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        limit: String(limit),
        offset: String(page * limit),
      });
      if (fraudOnly) params.set("is_fraud", "true");
      const res = await fetch(`${API_URL}/api/transactions?${params}`);
      const data = await res.json();
      setTransactions(data.transactions || []);
      setTotal(data.total || 0);
    } catch {
      /* fetch failed — leave list empty */
    }
    setLoading(false);
  }, [page, fraudOnly]);

  useEffect(() => {
    fetchTransactions();
  }, [fetchTransactions]);

  const startInvestigation = async (txnId: string) => {
    setInvestigating(txnId);
    try {
      const res = await fetch(`${API_URL}/api/investigate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transaction_id: txnId }),
      });
      const data = await res.json();
      window.location.href = `/investigate/${data.investigation_id}?txn=${txnId}`;
    } catch {
      setInvestigating(null);
    }
  };

  const totalPages = Math.ceil(total / limit);

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">Transaction Dashboard</h1>
          <p className="text-gray-500 text-sm mt-1">
            {total.toLocaleString()} transactions{" "}
            {fraudOnly ? "(flagged only)" : ""}
          </p>
        </div>
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 cursor-pointer">
            <div
              className={`w-10 h-5 rounded-full transition-colors ${fraudOnly ? "bg-red-600" : "bg-gray-700"}`}
              onClick={() => {
                setFraudOnly(!fraudOnly);
                setPage(0);
              }}
            >
              <div
                className={`w-5 h-5 bg-white rounded-full shadow transition-transform ${fraudOnly ? "translate-x-5" : ""}`}
              />
            </div>
            <span className="text-sm text-gray-400">Flagged Only</span>
          </label>
        </div>
      </div>

      <div className="card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[#262626] text-left text-gray-500">
                <th className="px-4 py-3 font-medium">ID</th>
                <th className="px-4 py-3 font-medium">Type</th>
                <th className="px-4 py-3 font-medium text-right">Amount</th>
                <th className="px-4 py-3 font-medium">From</th>
                <th className="px-4 py-3 font-medium">To</th>
                <th className="px-4 py-3 font-medium">Category</th>
                <th className="px-4 py-3 font-medium">Country</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Action</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td
                    colSpan={9}
                    className="px-4 py-8 text-center text-gray-500"
                  >
                    Loading...
                  </td>
                </tr>
              ) : transactions.length === 0 ? (
                <tr>
                  <td
                    colSpan={9}
                    className="px-4 py-8 text-center text-gray-500"
                  >
                    No transactions found
                  </td>
                </tr>
              ) : (
                transactions.map((txn) => (
                  <tr
                    key={txn.transaction_id}
                    className="border-b border-[#1a1a1a] hover:bg-[#1a1a1a] transition-colors"
                  >
                    <td className="px-4 py-3 font-mono text-xs">
                      {txn.transaction_id}
                    </td>
                    <td className="px-4 py-3">
                      <span className="px-2 py-0.5 rounded bg-[#1e1e1e] text-xs">
                        {txn.type}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right font-mono">
                      $
                      {txn.amount.toLocaleString(undefined, {
                        minimumFractionDigits: 2,
                        maximumFractionDigits: 2,
                      })}
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-400">
                      {txn.name_orig}
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-400">
                      {txn.name_dest}
                    </td>
                    <td className="px-4 py-3 text-xs">
                      {txn.merchant_category}
                    </td>
                    <td className="px-4 py-3 text-xs">{txn.country_code}</td>
                    <td className="px-4 py-3">
                      {txn.is_fraud ? (
                        <span className="badge-fraud">Fraud</span>
                      ) : (
                        <span className="badge-legit">Clean</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <button
                        onClick={() => startInvestigation(txn.transaction_id)}
                        disabled={investigating === txn.transaction_id}
                        className="btn-primary text-xs disabled:opacity-50"
                      >
                        {investigating === txn.transaction_id
                          ? "Starting..."
                          : "Investigate"}
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        <div className="flex items-center justify-between px-4 py-3 border-t border-[#262626]">
          <span className="text-sm text-gray-500">
            Page {page + 1} of {totalPages || 1}
          </span>
          <div className="flex gap-2">
            <button
              onClick={() => setPage(Math.max(0, page - 1))}
              disabled={page === 0}
              className="px-3 py-1 text-sm rounded bg-[#1e1e1e] hover:bg-[#2a2a2a] disabled:opacity-30 transition-colors"
            >
              Previous
            </button>
            <button
              onClick={() => setPage(Math.min(totalPages - 1, page + 1))}
              disabled={page >= totalPages - 1}
              className="px-3 py-1 text-sm rounded bg-[#1e1e1e] hover:bg-[#2a2a2a] disabled:opacity-30 transition-colors"
            >
              Next
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
