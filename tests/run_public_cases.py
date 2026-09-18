"""Run all public sample cases against the GridWise service.
Usage: python tests/run_public_cases.py [base_url] [cases_json_path]
"""
import json
import sys

import requests

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
CASES_PATH = sys.argv[2] if len(sys.argv) > 2 else \
    "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"
TOL = 0.01

with open(CASES_PATH) as f:
    CASES = json.load(f)["cases"]


def close(a, b, tol=TOL):
    return abs(a - b) <= tol


def check_directives(got, exp):
    errs = []
    if len(got) != len(exp):
        return [f"count {len(got)} != {len(exp)}"]
    for g, e in zip(got, exp):
        if g["note_index"] != e["note_index"]:
            errs.append(f"note_index {g['note_index']} != {e['note_index']}")
        if g["applies"] != e["applies"] or g["directive_type"] != e["directive_type"]:
            errs.append(f"note {e['note_index']}: applies/type mismatch")
        ga, ea = g["structured_adjustment"], e["structured_adjustment"]
        if (ga is None) != (ea is None):
            errs.append(f"note {e['note_index']}: adjustment null mismatch")
        elif ga:
            if ga.get("hours") != ea.get("hours"):
                errs.append(f"note {e['note_index']}: hours mismatch")
            for k in ("factor", "minimum_energy_kwh", "max_grid_kwh"):
                if k in (ea or {}):
                    if not close(float(ga.get(k, -1)), float(ea[k])):
                        errs.append(f"note {e['note_index']}: {k} mismatch")
    return errs


def replay(plan, inp, directives):
    errs = []
    b = inp["battery"]
    solar_eff = {h["hour"]: h["solar_kwh"] for h in inp["hours"]}
    demand = {h["hour"]: h["demand_kwh"] for h in inp["hours"]}
    min_res = {h: b["minimum_energy_kwh"] for h in range(24)}
    no_ch, no_dis, gcap = set(), set(), {}
    for d in directives:
        if not d["applies"]:
            continue
        a = d["structured_adjustment"]
        if d["directive_type"] == "solar_reduction":
            for h in a["hours"]:
                solar_eff[h] *= a["factor"]
        elif d["directive_type"] == "minimum_battery_reserve":
            for h in a["hours"]:
                min_res[h] = max(min_res[h], a["minimum_energy_kwh"])
        elif d["directive_type"] == "no_charge_window":
            no_ch.update(a["hours"])
        elif d["directive_type"] == "no_discharge_window":
            no_dis.update(a["hours"])
        elif d["directive_type"] == "max_grid_window":
            for h in a["hours"]:
                gcap[h] = a["max_grid_kwh"]

    soc = b["initial_energy_kwh"]
    for e in plan:
        h = e["hour"]
        ch = e["battery_kwh"] if e["battery_action"] == "charge" else 0.0
        dis = e["battery_kwh"] if e["battery_action"] == "discharge" else 0.0
        if e["battery_action"] == "idle" and abs(e["battery_kwh"]) > TOL:
            errs.append(f"h{h}: idle with battery_kwh={e['battery_kwh']}")
        if not close(e["grid_kwh"] + e["solar_used_kwh"] + dis, demand[h] + ch):
            errs.append(f"h{h}: energy balance broken")
        if e["solar_used_kwh"] > solar_eff[h] + TOL:
            errs.append(f"h{h}: solar_used exceeds effective solar")
        if h in no_ch and ch > TOL:
            errs.append(f"h{h}: charge during no_charge_window")
        if h in no_dis and dis > TOL:
            errs.append(f"h{h}: discharge during no_discharge_window")
        if h in gcap and e["grid_kwh"] > gcap[h] + TOL:
            errs.append(f"h{h}: grid exceeds cap")
        if ch > b["max_charge_kwh_per_hour"] + TOL:
            errs.append(f"h{h}: charge rate exceeded")
        if dis > b["max_discharge_kwh_per_hour"] + TOL:
            errs.append(f"h{h}: discharge rate exceeded")
        soc = soc + ch - dis
        if soc < min_res[h] - TOL or soc > b["capacity_kwh"] + TOL:
            errs.append(f"h{h}: soc {soc:.2f} out of bounds")
        if not close(e["battery_energy_after_kwh"], soc):
            errs.append(f"h{h}: reported soc mismatch")
    if not close(soc, b["initial_energy_kwh"]):
        errs.append(f"end-of-day soc {soc:.2f} != initial")
    return errs


passed = 0
for case in CASES:
    r = requests.post(f"{BASE_URL}/optimize-energy", json=case["input"], timeout=120)
    if r.status_code != 200:
        print(f"{case['id']}: HTTP {r.status_code} -> {r.text[:150]}")
        continue
    out = r.json()
    errs = check_directives(out["directive_interpretation"],
                            case["expected_output"]["directive_interpretation"])
    errs += replay(out["hourly_plan"], case["input"],
                   case["expected_output"]["directive_interpretation"])
    exp_cost = case["expected_output"]["total_cost_bdt"]
    got_cost = out["total_cost_bdt"]
    ok = not errs
    if ok:
        passed += 1
    ratio = exp_cost / got_cost if got_cost else 0
    print(f"{case['id']}: {'PASS' if ok else 'FAIL'} "
          f"cost={got_cost:.2f} vs ref={exp_cost:.2f} (quality={ratio:.4f})")
    for e in errs[:6]:
        print(f"    - {e}")

print(f"\n{passed}/{len(CASES)} cases valid")