"""Anthropic-style cost estimates from token usage (USD)."""

# Published-style pricing per 1M tokens (input, output)
MODEL_PRICING_PER_MILLION: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (0.80, 4.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    # Aliases / older ids
    "claude-3-5-haiku-20241022": (0.80, 4.00),
    "claude-3-5-sonnet-20241022": (3.00, 15.00),
}


def _price_for_model(model: str) -> tuple[float, float]:
    if model in MODEL_PRICING_PER_MILLION:
        return MODEL_PRICING_PER_MILLION[model]
    for key, prices in MODEL_PRICING_PER_MILLION.items():
        if key in model or model in key:
            return prices
    return MODEL_PRICING_PER_MILLION["claude-sonnet-4-5"]


def usage_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = _price_for_model(model)
    return (input_tokens / 1_000_000.0) * pin + (output_tokens / 1_000_000.0) * pout


def usage_record(
    agent: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> dict:
    cost = usage_cost_usd(model, input_tokens, output_tokens)
    return {
        "agent": agent,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost, 6),
    }
