# GridWise — LLM-Assisted Campus Energy Optimizer

BUP CSE Fest 2026 · Online Preliminary · Smart Campus Energy Optimization Challenge

**Live API:** https://bup-cse-fest-2026-gridwise.onrender.com

**Docker image:** `shaswatabarua/gridwise:latest`

## What this service does

Receives a 24-hour campus energy scenario (demand, solar, tariff, battery) plus 1–3 natural-language operator notes, then:

1. **Interprets** each note with a large language model (Groq `openai/gpt-oss-120b`) into a structured machine-checkable directive.
2. **Validates** the interpretation with deterministic guardrails. Invalid output is safely downgraded to `no_op` — the service never crashes.
3. **Optimizes** a 24-hour schedule (LP/MILP, PuLP + CBC) that minimizes total grid cost while satisfying energy balance, battery rules, all directives, and end-of-day neutrality.
4. Returns the interpretation + hourly plan + replay-derived totals as JSON.

Supported directives: `solar_reduction`, `minimum_battery_reserve`, `no_charge_window`, `no_discharge_window`, `max_grid_window`, `no_op`.

## Architecture

```
Notes → Groq LLM → Deterministic Guardrails → PuLP/CBC LP Optimizer → 24h Plan
```

If the LLM errors or returns malformed output, a small deterministic parser covers the six directive patterns so the service stays up. Identical requests are cached in-memory.

## Endpoints

- `GET /health` — readiness, returns `{"status":"ok"}`
- `POST /optimize-energy` — interpret notes + return optimal 24h plan

## Local quickstart

```
git clone https://github.com/ShaswataBarua/bup-cse-fest-2026-gridwise.git
cd bup-cse-fest-2026-gridwise
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export GROQ_API_KEY=your_key
uvicorn app.main:app --host 0.0.0.0 --port 8000
curl http://127.0.0.1:8000/health
```

## Environment variables

- `GROQ_API_KEY` — required, free Groq API key (console.groq.com/keys)
- `GROQ_MODEL` — optional, default `openai/gpt-oss-120b`
- `LLM_PROVIDER` — optional, `groq` (default) or `gemini`
- `GEMINI_API_KEY` / `GEMINI_MODEL` — optional Gemini backup provider

## Public sample test

Place `BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json` in the repo root, then run `python tests/run_public_cases.py http://127.0.0.1:8000`

Current result: **10/10 public cases valid, quality 1.0000 on every case.**

## Docker

```
docker build -t gridwise .
docker run -p 8000:8000 -e GROQ_API_KEY=your_key gridwise
```

Prebuilt image (Docker Hub):

```
docker pull shaswatabarua/gridwise:latest
docker run -p 8000:8000 -e GROQ_API_KEY=your_key shaswatabarua/gridwise:latest
```

## Deployment

Deployed on Render (free tier) from this repo's `Dockerfile`; the service binds `0.0.0.0:$PORT`. `GROQ_API_KEY` is injected via Render environment variables — no secrets are committed to the repository.

## Optimizer model

Per hour h (0..23): variables for grid purchase, solar used, battery charge/discharge, and battery state-of-charge. Constraints: energy balance, effective solar limit, battery bounds and rate limits, all operator directives, and end-of-day neutrality. Objective: minimize the sum of grid[h] × tariff[h]. Solved with CBC in under 1 second.

## Failure handling

- Malformed JSON / schema errors → `400 {"error":"invalid_request"}`
- LLM provider failure / bad model output → deterministic fallback interpretation
- Unexpected internal error → controlled `500` (no stack traces or secrets leaked)

## Known limitations

- The regex fallback only covers the six supported directive phrasings; it is a safety net, not a replacement for the LLM.
- Free-tier Groq has rate limits; the in-memory cache absorbs repeated identical scenarios.
- Free Render instances sleep after inactivity; the first request after a cold start can take ~30–50 s.

## Credits

Built with FastAPI, Pydantic, PuLP (CBC solver), Groq API (`openai/gpt-oss-120b`), google-generativeai (optional backup provider), requests.
