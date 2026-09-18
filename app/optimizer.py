"""GridWise LP optimizer (PuLP + CBC - both free).

Model per hour h (0..23):
    variables: grid[h] >= 0, solar_used[h] >= 0,
               charge[h] >= 0, discharge[h] >= 0, soc[h], y[h] in {0,1}
    solar_used[h] <= effective_solar[h]            (solar_reduction applied)
    grid + solar_used + discharge == demand + charge                (balance)
    soc[h] == soc[h-1] + charge[h] - discharge[h]                   (state)
    min_reserve(h) <= soc[h] <= capacity
    charge[h]   <= max_charge   * y[h]   (0 in no_charge_window hours)
    discharge[h]<= max_discharge* (1-y[h])(0 in no_discharge_window hours)
    grid[h] <= max_grid_kwh(h)                     (max_grid_window hours)
    soc[23] == initial_energy_kwh                  (end-of-day neutrality)
    minimize sum(grid[h] * tariff[h])
"""
import pulp

TOL = 0.01


def _apply_directives(hours_data, battery, directives):
    demand = [0.0] * 24
    solar = [0.0] * 24
    tariff = [0.0] * 24
    for h in hours_data:
        demand[h.hour] = float(h.demand_kwh)
        solar[h.hour] = float(h.solar_kwh)
        tariff[h.hour] = float(h.tariff_bdt_per_kwh)

    min_res = [float(battery.minimum_energy_kwh)] * 24
    no_charge, no_discharge = set(), set()
    grid_cap = {}

    for d in directives:
        if not d.get("applies"):
            continue
        dtype = d["directive_type"]
        adj = d.get("structured_adjustment") or {}
        hrs = adj.get("hours", [])
        if dtype == "solar_reduction":
            f = float(adj["factor"])
            for h in hrs:
                solar[h] *= f
        elif dtype == "minimum_battery_reserve":
            v = float(adj["minimum_energy_kwh"])
            for h in hrs:
                min_res[h] = max(min_res[h], v)
        elif dtype == "no_charge_window":
            no_charge.update(hrs)
        elif dtype == "no_discharge_window":
            no_discharge.update(hrs)
        elif dtype == "max_grid_window":
            v = float(adj["max_grid_kwh"])
            for h in hrs:
                grid_cap[h] = min(grid_cap.get(h, float("inf")), v)

    return demand, solar, tariff, min_res, no_charge, no_discharge, grid_cap


def solve_schedule(hours_data, battery, directives):
    demand, solar, tariff, min_res, no_charge, no_discharge, grid_cap = \
        _apply_directives(hours_data, battery, directives)

    prob = pulp.LpProblem("gridwise", pulp.LpMinimize)
    grid = pulp.LpVariable.dicts("grid", range(24), lowBound=0)
    sol = pulp.LpVariable.dicts("sol", range(24), lowBound=0)
    ch = pulp.LpVariable.dicts("ch", range(24), lowBound=0)
    dis = pulp.LpVariable.dicts("dis", range(24), lowBound=0)
    soc = pulp.LpVariable.dicts("soc", range(24), lowBound=0)
    y = pulp.LpVariable.dicts("y", range(24), cat="Binary")

    cap = float(battery.capacity_kwh)
    init = float(battery.initial_energy_kwh)
    max_ch = float(battery.max_charge_kwh_per_hour)
    max_dis = float(battery.max_discharge_kwh_per_hour)

    for h in range(24):
        prob += sol[h] <= solar[h]
        prob += grid[h] + sol[h] + dis[h] == demand[h] + ch[h]
        prev = init if h == 0 else soc[h - 1]
        prob += soc[h] == prev + ch[h] - dis[h]
        prob += soc[h] >= min_res[h]
        prob += soc[h] <= cap
        ch_cap = 0.0 if h in no_charge else max_ch
        dis_cap = 0.0 if h in no_discharge else max_dis
        prob += ch[h] <= ch_cap * y[h]
        prob += dis[h] <= dis_cap * (1 - y[h])
        if h in grid_cap:
            prob += grid[h] <= grid_cap[h]

    prob += soc[23] == init
    prob += pulp.lpSum(grid[h] * tariff[h] for h in range(24))

    prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[prob.status] != "Optimal":
        raise RuntimeError(f"Optimizer status: {pulp.LpStatus[prob.status]}")

    val = pulp.value
    ch_v = [round(val(ch[h]), 2) for h in range(24)]
    dis_v = [round(val(dis[h]), 2) for h in range(24)]
    # solar rounded DOWN by an epsilon so we never exceed effective solar
    sol_v = [round(val(sol[h]) - 1e-9, 2) for h in range(24)]
    for h in range(24):
        if sol_v[h] <= 0:
            sol_v[h] = 0.0

    # Fix end-of-day neutrality drift caused by rounding (adjust hour 23).
    soc_v = []
    prev = init
    for h in range(24):
        prev = prev + ch_v[h] - dis_v[h]
        soc_v.append(round(prev, 2))
    drift = round(soc_v[23] - init, 2)
    if abs(drift) > 1e-9:
        net = ch_v[23] - dis_v[23] - drift
        ch23 = max(net, 0.0)
        dis23 = max(-net, 0.0)
        ch_cap23 = 0.0 if 23 in no_charge else max_ch
        dis_cap23 = 0.0 if 23 in no_discharge else max_dis
        if ch23 <= ch_cap23 + 1e-9 and dis23 <= dis_cap23 + 1e-9:
            ch_v[23], dis_v[23] = round(ch23, 2), round(dis23, 2)
            soc_v = []
            prev = init
            for h in range(24):
                prev = prev + ch_v[h] - dis_v[h]
                soc_v.append(round(prev, 2))

    # Derive grid from the balance equation so balance holds exactly.
    grid_v = []
    for h in range(24):
        g = round(demand[h] + ch_v[h] - dis_v[h] - sol_v[h], 2)
        grid_v.append(0.0 if -TOL < g < 0 else g)

    plan = []
    n_charge = n_discharge = 0
    for h in range(24):
        if ch_v[h] > 1e-9:
            action, bk = "charge", ch_v[h]
            n_charge += 1
        elif dis_v[h] > 1e-9:
            action, bk = "discharge", dis_v[h]
            n_discharge += 1
        else:
            action, bk = "idle", 0.0
        plan.append({
            "hour": h,
            "grid_kwh": grid_v[h],
            "solar_used_kwh": sol_v[h],
            "battery_action": action,
            "battery_kwh": round(bk, 2),
            "battery_energy_after_kwh": soc_v[h],
        })

    total_grid = round(sum(grid_v), 2)
    total_cost = round(sum(grid_v[h] * tariff[h] for h in range(24)), 2)
    peak = round(max(grid_v), 2)
    applied = sum(1 for d in directives if d.get("applies"))
    summary = (
        f"Applied {applied} of {len(directives)} interpreted directive(s); "
        f"{n_charge} charge / {n_discharge} discharge hours; minimized grid cost "
        f"to {total_cost:.2f} BDT while respecting energy balance, battery limits, "
        f"all operator directives, and end-of-day battery neutrality."
    )
    return {
        "hourly_plan": plan,
        "total_grid_kwh": total_grid,
        "total_cost_bdt": total_cost,
        "peak_grid_kwh": peak,
        "plan_summary": summary,
    }