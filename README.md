# bup-cse-fest-2026-gridwise
# GridWise — LLM-Assisted Campus Energy Optimizer

BUP CSE Fest 2026 · Online Preliminary · Smart Campus Energy Optimization Challenge

**Live API:** https://bup-cse-fest-2026-gridwise.onrender.com

## What this service does

Receives a 24-hour campus energy scenario (demand, solar, tariff, battery) plus 1–3
natural-language operator notes, then:

1. **Interprets** each note with a large language model (Groq `openai/gpt-oss-120b`)
   into a structured machine-checkable directive.
2. **Validates** the interpretation with deterministic guardrails (allowed directive
   types, note coverage, hour ranges, numeric bounds, `no_op` semantics). Invalid
   output is safely downgraded to `no_op` — the service never crashes.
3. **Optimizes** a 24-hour operating schedule (LP/MILP, PuLP + CBC solver) that
   minimizes total grid electricity cost while satisfying energy balance, effective
   solar, battery bounds/rate limits, every applicable directive, and end-of-day
   battery neutrality.
4. Returns the interpretation + hourly plan + replay-derived totals as JSON.

Supported directives: `solar_reduction`, `minimum_battery_reserve`,
`no_charge_window`, `no_discharge_window`, `max_grid_window`, `no_op`.

## Architecture
