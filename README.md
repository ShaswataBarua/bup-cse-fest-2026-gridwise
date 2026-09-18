# GridWise — LLM-Assisted Campus Energy Optimizer

BUP CSE Fest 2026 · Online Preliminary · Smart Campus Energy Optimization Challenge

**Live API:** https://bup-cse-fest-2026-gridwise.onrender.com

**Docker image:** `shaswatabarua/gridwise:latest`

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
Energy data + operator notes
│
▼
LLM Interpreter (Groq, temperature 0, strict JSON)
│
▼
Deterministic Guardrails (schema/hours/range checks)
│
▼
LP Optimizer (PuLP + CBC):  min Σ grid[h]·tariff[h]
│
▼
Response (hourly_plan, totals, plan_summary)
plain

Fallbacks: if the LLM provider errors or returns malformed output, a small
deterministic parser covers the six supported directive patterns so the service
stays up (the LLM remains the primary path). Identical requests are cached
in-memory for low latency.

## Endpoints

| Endpoint | Description |
|---|---|
| `GET /health` | Readiness: `{"status":"ok"}` |
| `POST /optimize-energy` | Interpret notes + return optimal 24h plan |

## Local quickstart

```bash
git clone https://github.com/ShaswataBarua/bup-cse-fest-2026-gridwise.git
cd bup-cse-fest-2026-gridwise
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export GROQ_API_KEY=your_key          # free key: https://console.groq.com/keys
uvicorn app.main:app --host 0.0.0.0 --port 8000
Test:
bash
curl http://127.0.0.1:8000/health
# → {"status":"ok"}
Environment variables
Table
Name	Required	Default	Purpose
GROQ_API_KEY	yes	—	Free Groq API key
GROQ_MODEL	no	openai/gpt-oss-120b	Groq chat model
LLM_PROVIDER	no	groq	groq or gemini
GEMINI_API_KEY	no	—	Alternative free provider
GEMINI_MODEL	no	gemini-2.0-flash	Gemini model
Public sample test
Place BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json in the repo root, then:
bash
python tests/run_public_cases.py http://127.0.0.1:8000
Current result: 10/10 public cases valid, quality 1.0000 on every case
(checked against the official reference schedules).
Docker
Build and run locally:
bash
docker build -t gridwise .
docker run -p 8000:8000 -e GROQ_API_KEY=your_key gridwise
curl http://127.0.0.1:8000/health
Prebuilt image (Docker Hub):
bash
docker pull shaswatabarua/gridwise:latest
docker run -p 8000:8000 -e GROQ_API_KEY=your_key shaswatabarua/gridwise:latest
Deployment
Deployed on Render (free tier) from this repo's Dockerfile; the service binds
0.0.0.0:$PORT. GROQ_API_KEY is injected via Render environment variables —
no secrets are committed to the repository.
Optimizer model
Per hour h ∈ 0..23, variables: grid[h] ≥ 0, solar_used[h] ≥ 0,
charge[h] ≥ 0, discharge[h] ≥ 0, soc[h], binary y[h].
solar_used[h] ≤ effective_solar[h] (after solar_reduction)
grid + solar_used + discharge = demand + charge (energy balance)
soc[h] = soc[h−1] + charge[h] − discharge[h]
min_reserve(h) ≤ soc[h] ≤ capacity (minimum_battery_reserve raises the floor)
charge[h] ≤ max_charge·y[h], discharge[h] ≤ max_discharge·(1−y[h])
(zero in no_charge_window / no_discharge_window hours)
grid[h] ≤ max_grid_kwh in max_grid_window hours
soc[23] = initial (end-of-day neutrality)
Objective: minimize Σ grid[h]·tariff[h]. Solved with CBC (branch-and-bound
on 24 binary variables, typically < 1 s).
Failure handling
Malformed JSON / schema errors → 400 {"error":"invalid_request"}
LLM provider failure / bad model output → deterministic fallback interpretation
Unexpected internal error → controlled 500 (no stack traces or secrets leaked)
Known limitations
The regex fallback only covers the six supported directive phrasings; it is a
safety net, not a replacement for the LLM.
Free-tier Groq has rate limits (~requests/minute); the in-memory cache absorbs
repeated identical scenarios.
Free Render instances sleep after inactivity; the first request after a cold
start can take ~30–50 s.
Credits
Built with FastAPI, Pydantic, PuLP (CBC solver), Groq API (openai/gpt-oss-120b),
google-generativeai (optional backup provider), requests.
plain

**Steps:**
1. In VS Code `README.md`: put your cursor at the very end of the file (after `## Architecture`)
2. Press Enter, paste Part 2
3. Save → the status bar should show roughly **Ln 150+** now
4. Push:
```bash
git add README.md
git commit -m "Full README: quickstart, env vars, tests, Docker fallback"
git push
