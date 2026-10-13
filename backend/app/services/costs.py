"""Versioned provider pricing estimates, never a substitute for a provider invoice."""

from datetime import date

PRICE_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing/"
PRICE_CHECKED_AT = "2026-10-12"
PRICE_VALID_THROUGH = date(2026, 11, 11)
# USD per million tokens. Low=off-peak, high=peak; do not infer Chinese holidays.
DEEPSEEK = {
    "deepseek-flash": {"input": (0.15, 0.30), "cache": (0.003, 0.006), "output": (0.60, 1.20)},
    "deepseek-v4-pro": {"input": (0.66, 1.32), "cache": (0.022, 0.044), "output": (1.98, 3.96)},
}


def estimate_cost(settings, model, usage, *, usage_available=True):
    if settings.agent_mode == "demo":
        return {"estimated_cost_usd": 0.0, "cost_lower_usd": 0.0, "cost_upper_usd": 0.0,
                "cost_complete": True, "cost_basis": "offline-demo-no-api", "cost_is_invoice": False}
    stage = usage.get("stage")
    input_rate, output_rate = settings.model_costs_for_stage(stage)
    if input_rate > 0 and output_rate > 0:
        rates = {"input": (input_rate, input_rate), "cache": (input_rate, input_rate), "output": (output_rate, output_rate)}
        source, checked = "explicit-user-rates-uncached-input-estimate", None
    else:
        alias = "deepseek-flash" if model in {"deepseek-v4-flash", "deepseek-v4-flash-vision-exp"} else model
        rates = DEEPSEEK.get(alias) if settings.agent_mode == "deepseek" and date.today() <= PRICE_VALID_THROUGH else None
        source, checked = PRICE_SOURCE, PRICE_CHECKED_AT
    result = {"estimated_cost_usd": None, "cost_lower_usd": None, "cost_upper_usd": None,
              "cost_complete": False, "cost_basis": "unknown-usage-or-rate", "cost_is_invoice": False,
              "price_source": source, "price_checked_at": checked, "price_valid_through": PRICE_VALID_THROUGH.isoformat()}
    if not rates or not usage_available:
        return result
    inp, out = usage.get("input_tokens"), usage.get("output_tokens")
    if not isinstance(inp, int) or not isinstance(out, int) or inp < 0 or out < 0:
        return result
    hit = usage.get("cache_hit_tokens")
    miss = usage.get("cache_miss_tokens")
    cache_known = isinstance(hit, int) and hit >= 0 and hit <= inp
    if cache_known:
        miss = inp - hit if miss is None else miss
        cache_known = isinstance(miss, int) and miss >= 0 and hit + miss == inp
    low = (hit * rates["cache"][0] + miss * rates["input"][0] + out * rates["output"][0]) / 1e6 if cache_known else (inp * rates["cache"][0] + out * rates["output"][0]) / 1e6
    high = (hit * rates["cache"][1] + miss * rates["input"][1] + out * rates["output"][1]) / 1e6 if cache_known else (inp * rates["input"][1] + out * rates["output"][1]) / 1e6
    result.update(estimated_cost_usd=round(high, 8), cost_lower_usd=round(low, 8), cost_upper_usd=round(high, 8),
                  cost_complete=True, cache_usage_known=cache_known, rates=rates,
                  cost_basis="configured-uncached-estimate" if checked is None else "peak-upper-bound-with-off-peak-lower-bound")
    return result
