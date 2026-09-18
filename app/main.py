"""GridWise LLM-assisted energy optimization service (BUP CSE Fest 2026 prelim)."""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .guardrails import validate_interpretations
from .llm_interpreter import interpret_notes
from .optimizer import solve_schedule
from .schemas import ScenarioRequest, OptimizeResponse

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("gridwise")

app = FastAPI(title="GridWise LLM Energy Optimizer", version="1.0.0")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/optimize-energy", response_model=OptimizeResponse)
def optimize_energy(req: ScenarioRequest):
    try:
        notes = [n for n in req.operator_notes if n and n.strip()]
        raw, source = interpret_notes(notes, req.battery.capacity_kwh)
        log.info("interpretation source=%s scenario=%s", source, req.scenario_id)

        validated = validate_interpretations(
            raw, len(notes), req.battery.capacity_kwh)

        plan = solve_schedule(req.hours, req.battery, validated)

        return {
            "scenario_id": req.scenario_id,
            "directive_interpretation": [
                {
                    "note_index": e["note_index"],
                    "applies": e["applies"],
                    "directive_type": e["directive_type"],
                    "structured_adjustment": e["structured_adjustment"],
                    "explanation": e["explanation"],
                }
                for e in validated
            ],
            **plan,
        }
    except Exception as exc:
        log.exception("optimization failed: %s", exc)
        return JSONResponse(status_code=500,
                            content={"error": "internal_error",
                                     "message": "Failed to produce an optimization plan."})


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400,
                        content={"error": "invalid_request",
                                 "detail": "Request does not match the GridWise schema."})