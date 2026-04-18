"""LangGraph orchestrator: wires 3 specialist agents into a state machine."""

import asyncio
import time
from typing import Any, Callable, Optional, TypedDict

from langgraph.graph import StateGraph, END

from app.agents.triage_agent import run_triage
from app.agents.behavior_agent import run_behavior
from app.agents.synthesis_agent import run_synthesis


class InvestigationState(TypedDict, total=False):
    transaction_id: str
    investigation_id: str
    triage_result: dict
    behavior_result: Optional[dict]
    synthesis_result: Optional[dict]
    emit_event: Any
    get_approval: Any
    final_result: dict


def build_investigation_graph():
    """Build the LangGraph state machine for fraud investigation."""

    async def triage_node(state: InvestigationState) -> dict:
        emit = state.get("emit_event")
        result = await run_triage(
            transaction_id=state["transaction_id"],
            emit_event=emit,
            investigation_id=state.get("investigation_id"),
        )
        return {"triage_result": result}

    async def behavior_node(state: InvestigationState) -> dict:
        emit = state.get("emit_event")
        result = await run_behavior(
            transaction_id=state["transaction_id"],
            triage_result=state["triage_result"],
            emit_event=emit,
            investigation_id=state.get("investigation_id"),
        )
        return {"behavior_result": result}

    async def synthesis_node(state: InvestigationState) -> dict:
        emit = state.get("emit_event")
        result = await run_synthesis(
            transaction_id=state["transaction_id"],
            investigation_id=state["investigation_id"],
            triage_result=state["triage_result"],
            behavior_result=state.get("behavior_result"),
            emit_event=emit,
        )
        return {"synthesis_result": result, "final_result": result}

    def should_run_behavior(state: InvestigationState) -> str:
        """Route to behavior when triage is uncertain or risky; skip for clear legitimate."""
        triage = state.get("triage_result", {})
        confidence = float(triage.get("confidence", 0.0))
        verdict = triage.get("verdict", "")
        rules = triage.get("rules_fired", [])
        strong = any(r.get("severity") in ("HIGH", "CRITICAL") for r in rules)

        if verdict == "legitimate":
            return "synthesis"
        if verdict == "likely_legitimate" and confidence >= 0.84 and not strong:
            return "synthesis"
        if confidence < 0.9:
            return "behavior"
        return "synthesis"

    graph = StateGraph(InvestigationState)

    graph.add_node("triage", triage_node)
    graph.add_node("behavior", behavior_node)
    graph.add_node("synthesis", synthesis_node)

    graph.set_entry_point("triage")
    graph.add_conditional_edges("triage", should_run_behavior, {"behavior": "behavior", "synthesis": "synthesis"})
    graph.add_edge("behavior", "synthesis")
    graph.add_edge("synthesis", END)

    return graph.compile()


investigation_graph = build_investigation_graph()


async def run_investigation(
    transaction_id: str,
    investigation_id: str,
    emit_event: Callable[[dict], None] | None = None,
    get_approval: Callable[[], Optional[str]] | None = None,
) -> dict:
    """Run the full investigation pipeline through LangGraph."""
    start = time.time()

    if emit_event:
        emit_event({"agent": "orchestrator", "type": "thought", "content": f"Starting investigation {investigation_id} for transaction {transaction_id}"})
        emit_event({"agent": "orchestrator", "type": "thought", "content": "Pipeline: Triage → [Behavior if needed] → Synthesis"})

    initial_state: InvestigationState = {
        "transaction_id": transaction_id,
        "investigation_id": investigation_id,
        "emit_event": emit_event,
        "get_approval": get_approval,
    }

    result = await investigation_graph.ainvoke(initial_state)

    final = dict(result.get("final_result", result.get("synthesis_result", {})))
    triage_r = result.get("triage_result") or {}
    beh_r = result.get("behavior_result")
    synth_r = result.get("synthesis_result") or final
    usages = []
    for sub in (triage_r, beh_r or {}, synth_r):
        u = (sub or {}).get("llm_usage")
        if u:
            usages.append(u)
    tin = sum(int(x.get("input_tokens", 0)) for x in usages)
    tout = sum(int(x.get("output_tokens", 0)) for x in usages)
    tcost = round(sum(float(x.get("cost_usd", 0)) for x in usages), 6)
    final["triage"] = triage_r
    final["behavior"] = beh_r
    final["synthesis"] = synth_r
    final["model_breakdown"] = usages
    final["token_usage"] = {"input_tokens": tin, "output_tokens": tout}
    final["cost_usd"] = tcost

    # Wait for approval if needed
    if final.get("requires_approval") and get_approval:
        if emit_event:
            emit_event({"agent": "orchestrator", "type": "thought", "content": "Waiting for human approval..."})

        max_wait = 300  # 5 minutes max
        waited = 0
        while waited < max_wait:
            decision = get_approval()
            if decision:
                final["approval_decision"] = decision
                if emit_event:
                    emit_event({"agent": "orchestrator", "type": "thought", "content": f"Approval decision received: {decision}"})
                break
            await asyncio.sleep(1)
            waited += 1

        if waited >= max_wait:
            final["approval_decision"] = "timeout"
            if emit_event:
                emit_event({"agent": "orchestrator", "type": "thought", "content": "Approval timeout — auto-escalating"})

    total_ms = int((time.time() - start) * 1000)
    final["total_latency_ms"] = total_ms

    if emit_event:
        emit_event({"agent": "orchestrator", "type": "thought", "content": f"Investigation complete in {total_ms}ms"})

    return final
