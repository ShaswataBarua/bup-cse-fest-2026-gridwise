"""LLM operator-note interpretation.

PRIMARY PATH: a hosted generative model (Groq or Gemini, both free tiers)
converts natural-language notes into structured directives.

RESILIENCE FALLBACK: if and only if the model call fails (missing key,
provider error, malformed output), a small deterministic parser handles the
call so the service never crashes (safe-failure rule). The LLM remains the
primary interpretation path in normal operation.
"""
import json
import re

from .config import get_settings

PROMPT_TEMPLATE = """You are the GridWise operator-note interpreter for a campus energy scheduler.

Convert each campus operator note into exactly ONE structured directive.

Allowed directive_type values and required structured_adjustment shapes:
- solar_reduction: {"hours": [...], "factor": number}
    factor = the fraction of solar that REMAINS usable.
    "80% reduction" -> 0.2 | "drops to 25%" -> 0.25 | "about half" -> 0.5
- minimum_battery_reserve: {"hours": [...], "minimum_energy_kwh": number}
    If the note states a percentage of battery capacity, convert it to kWh
    using the battery capacity given below.
- no_charge_window: {"hours": [...]}
- no_discharge_window: {"hours": [...]}
- max_grid_window: {"hours": [...], "max_grid_kwh": number}
- no_op: structured_adjustment MUST be null (notes that do not affect today's
    24-hour energy schedule, e.g. mentions of next week, other offices, menus).

Rules:
- Time windows are start-inclusive and end-exclusive whole hours (0-23).
  "1 PM to 3 PM" -> [13, 14]. "from 6 PM until 9 PM" -> [18, 19, 20].
  "noon" = 12, "midnight" = 0. "13:00 to 15:00" -> [13, 14].
- Every non-no_op directive must have applies = true.
- Never invent demand/solar/tariff/battery values or unsupported directive types.
- Battery capacity: __CAPACITY__ kWh.

Return ONLY a JSON array with one object per note, in note order, each:
{"note_index": int, "applies": bool, "directive_type": str,
 "structured_adjustment": object-or-null, "explanation": short-string}

Notes:
__NOTES__"""

# ---------------------------------------------------------------- cache
_CACHE = {}


def interpret_notes(notes, battery_capacity):
    """Returns (parsed_list, source) where source is 'llm' or 'fallback'."""
    key = (tuple(notes), float(battery_capacity))
    if key in _CACHE:
        return _CACHE[key]
    settings = get_settings()
    result = None
    try:
        prompt = _build_prompt(notes, battery_capacity)
        if settings.llm_provider == "gemini":
            raw = _call_gemini(prompt, settings)
        else:
            raw = _call_groq(prompt, settings)
        parsed = _extract_json_array(raw)
        if parsed is None:
            raise ValueError("LLM output was not a JSON array")
        result = (parsed, "llm")
    except Exception:
        result = (_heuristic_fallback(notes, battery_capacity), "fallback")
    _CACHE[key] = result
    return result


def _build_prompt(notes, capacity):
    notes_block = "\n".join(f'{i}: "{n}"' for i, n in enumerate(notes))
    return (
        PROMPT_TEMPLATE.replace("__CAPACITY__", str(capacity))
        .replace("__NOTES__", notes_block)
    )


def _call_groq(prompt, settings):
    from groq import Groq

    client = Groq(api_key=settings.groq_api_key)
    # Groq retires model names over time - try candidates in order.
    candidates = [
        settings.groq_model,
        "llama-3.1-8b-instant",
        "openai/gpt-oss-120b",
        "meta-llama/llama-4-scout-17b-16e-instruct",
    ]
    last_err = None
    for model in dict.fromkeys(candidates):  # dedupe, keep order
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You output only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_tokens=2000,
            )
            return resp.choices[0].message.content
        except Exception as exc:
            last_err = exc
    raise last_err


def _call_gemini(prompt, settings):
    import google.generativeai as genai

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(settings.gemini_model)
    return model.generate_content(prompt).text


def _extract_json_array(text):
    if not text:
        return None
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


# ------------------------------------------------- deterministic fallback
def _heuristic_fallback(notes, capacity):
    return [_heuristic_note(i, n, capacity) for i, n in enumerate(notes)]


def _entry(i, applies, dtype, adj, expl):
    return {"note_index": i, "applies": applies, "directive_type": dtype,
            "structured_adjustment": adj, "explanation": expl}


def _heuristic_note(i, note, capacity):
    text = note.lower()
    hours = _extract_hours(text)

    if (
        re.search(r"\b(grid|feeder|transformer|substation|import|intake)\b", text)
        and re.search(r"(limit|cap|not exceed|at or below|no more than|must stay|maximum)", text)
        and hours
    ):
        m = re.search(r"(\d+(?:\.\d+)?)\s*kwh", text)
        if m:
            return _entry(i, True, "max_grid_window",
                          {"hours": hours, "max_grid_kwh": float(m.group(1))},
                          "Grid import is capped in the stated window.")

    if re.search(r"(solar|panel|\bpv\b|rooftop|inverter|cloud)", text) and \
            re.search(r"(reduction|reduce|drop|wash|clean|cloud|maintenance|cover|leave|lost|lower|inspection)", text) and hours:
        return _entry(i, True, "solar_reduction",
                      {"hours": hours, "factor": _parse_factor(text)},
                      "Usable solar is reduced in the stated window.")

    if re.search(r"(reserve|keep|remain|emergency|stored)", text) and \
            re.search(r"(battery|kwh|%)", text) and hours:
        m = re.search(r"(\d+(?:\.\d+)?)\s*kwh", text)
        if m:
            return _entry(i, True, "minimum_battery_reserve",
                          {"hours": hours, "minimum_energy_kwh": float(m.group(1))},
                          "Battery must stay above the stated reserve.")
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
        if m:
            return _entry(i, True, "minimum_battery_reserve",
                          {"hours": hours,
                           "minimum_energy_kwh": round(float(m.group(1)) / 100.0 * capacity, 2)},
                          "Percentage reserve converted to kWh.")

    if re.search(r"\bdischarg\w*", text) and \
            re.search(r"(not|no|unavailable|disabled|forbid|prohibit|avoid|zero)", text) and hours:
        return _entry(i, True, "no_discharge_window", {"hours": hours},
                      "Battery discharge is disabled in the stated window.")

    if re.search(r"\bcharg\w*", text) and \
            re.search(r"(not|no|unavailable|disabled|isolated|forbid|prohibit|avoid|maintenance)", text) and hours:
        return _entry(i, True, "no_charge_window", {"hours": hours},
                      "Battery charging is disabled in the stated window.")

    return _entry(i, False, "no_op", None,
                  "This note does not affect today's energy schedule.")


def _parse_factor(text):
    m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*reduction", text) or \
        re.search(r"reduction\s+(?:of\s+)?(\d+(?:\.\d+)?)\s*%", text)
    if m:
        return round(1.0 - float(m.group(1)) / 100.0, 4)
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*%\s*of\s+(?:the\s+)?(forecast|normal|usual|output)", text)
    if m:
        return round(float(m.group(1)) / 100.0, 4)
    m = re.search(
        r"(?:drop|drops|dropped|fall|falls|leave|leaves|left)\w*\s+"
        r"(?:to|at|around|about|roughly)\s+(\d+(?:\.\d+)?)\s*%", text)
    if m:
        return round(float(m.group(1)) / 100.0, 4)
    if "one-fifth" in text or "one fifth" in text or "20%" in text:
        return 0.2
    if "quarter" in text or "25%" in text:
        return 0.25
    if "half" in text or "50%" in text:
        return 0.5
    if "three-quarter" in text or "75%" in text:
        return 0.25
    return 0.5


def _extract_hours(text):
    tokens = []

    def add(pos, hour):
        if 0 <= hour <= 23:
            tokens.append((pos, hour))

    for m in re.finditer(r"(\d{1,2})(?::\d{2})?\s*(am|pm)\b", text):
        h = int(m.group(1))
        if m.group(2) == "pm" and h != 12:
            h += 12
        if m.group(2) == "am" and h == 12:
            h = 0
        add(m.start(), h)
    for m in re.finditer(r"\bnoon\b", text):
        add(m.start(), 12)
    for m in re.finditer(r"\bmidnight\b", text):
        add(m.start(), 0)
    for m in re.finditer(r"\b([01]?\d|2[0-3]):00\b", text):
        add(m.start(), int(m.group(1)))

    tokens.sort()
    seen, uniq = set(), []
    for pos, h in tokens:
        if h not in seen:
            seen.add(h)
            uniq.append((pos, h))

    if len(uniq) >= 2:
        start, end = uniq[0][1], uniq[1][1]
        if end <= start:
            end = start + 1
        return list(range(start, min(end, 24)))
    if len(uniq) == 1:
        return [uniq[0][1]]
    return None