"""Deterministic guardrails: LLM output is untrusted until it passes here.

Any directive that fails validation is downgraded to no_op rather than
allowed to corrupt the optimization model (safe-failure rule)."""
import math


ALLOWED_TYPES = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
}


def _no_op(i, expl):
    return {
        "note_index": i,
        "applies": False,
        "directive_type": "no_op",
        "structured_adjustment": None,
        "explanation": expl,
    }


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _clean_hours(raw):
    """Unique ints 0..23 in ascending order, or None if invalid."""
    if not isinstance(raw, list) or not raw:
        return None
    hours = []
    for h in raw:
        if isinstance(h, bool) or not isinstance(h, (int, float)) or not float(h).is_integer():
            return None
        h = int(h)
        if h < 0 or h > 23 or h in hours:
            return None
        hours.append(h)
    return sorted(hours)


def validate_interpretations(parsed, note_count, battery_capacity):
    """Returns one validated entry per note, in note_index order.

    parsed: raw list from the LLM (or fallback). Entries that are malformed,
    have bad hours, or out-of-range numbers are safely downgraded to no_op.
    """
    by_index = {}
    if isinstance(parsed, list):
        for e in parsed:
            if isinstance(e, dict) and isinstance(e.get("note_index"), int):
                by_index.setdefault(e["note_index"], e)

    validated = []
    for i in range(note_count):
        entry = by_index.get(i)
        if entry is None:
            validated.append(_no_op(i, "Note could not be interpreted; treated as no_op."))
            continue

        dtype = entry.get("directive_type")
        if dtype not in ALLOWED_TYPES or dtype == "no_op":
            validated.append(_no_op(
                i, str(entry.get("explanation") or "Note does not affect today's energy schedule.")))
            continue

        adj = entry.get("structured_adjustment")
        if not isinstance(adj, dict):
            validated.append(_no_op(i, "Directive failed validation; treated as no_op."))
            continue

        hours = _clean_hours(adj.get("hours"))
        if hours is None:
            validated.append(_no_op(i, "Directive hours failed validation; treated as no_op."))
            continue

        if dtype == "solar_reduction":
            f = adj.get("factor")
            if not _is_num(f) or not (0.0 <= f <= 1.0):
                validated.append(_no_op(i, "Solar factor failed validation; treated as no_op."))
                continue
            adj2 = {"hours": hours, "factor": float(f)}

        elif dtype == "minimum_battery_reserve":
            v = adj.get("minimum_energy_kwh")
            if not _is_num(v) or v < 0 or v > battery_capacity:
                validated.append(_no_op(i, "Reserve value failed validation; treated as no_op."))
                continue
            adj2 = {"hours": hours, "minimum_energy_kwh": float(v)}

        elif dtype == "max_grid_window":
            v = adj.get("max_grid_kwh")
            if not _is_num(v) or v < 0:
                validated.append(_no_op(i, "Grid cap failed validation; treated as no_op."))
                continue
            adj2 = {"hours": hours, "max_grid_kwh": float(v)}

        else:  # no_charge_window / no_discharge_window
            adj2 = {"hours": hours}

        validated.append({
            "note_index": i,
            "applies": True,
            "directive_type": dtype,
            "structured_adjustment": adj2,
            "explanation": str(entry.get("explanation", ""))[:300],
        })

    return validated