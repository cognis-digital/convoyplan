"""Core sustainment engine for CONVOYPLAN.

Reproducible logistics math:
  - per-leg fuel burn from distance, terrain, and load multipliers
  - cumulative fuel state vs. onboard + carried reserve capacity
  - resupply windows: where the convoy must refuel before running dry
  - chokepoint risk scoring (additive, bounded 0..100) from threat,
    congestion, and detour penalty inputs

No network, no external deps. A tiny dependency-free YAML subset parser
is included so plans can be written as readable logistics-as-code.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


class PlanError(Exception):
    """Raised when a plan is malformed or sustainment is infeasible."""


# ---------------------------------------------------------------------------
# Minimal YAML-subset parser (mappings, lists, scalars, nesting by indent).
# Sufficient for CONVOYPLAN plan files; no anchors/flow/multiline.
# ---------------------------------------------------------------------------

def _coerce(token: str) -> Any:
    t = token.strip()
    if t == "":
        return None
    if (t[0], t[-1]) in (("\"", "\""), ("'", "'")) and len(t) >= 2:
        return t[1:-1]
    low = t.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "none", "~"):
        return None
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        pass
    return t


def _strip_comment(line: str) -> str:
    out = []
    quote = None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out)


def _parse_yaml(text: str) -> Any:
    raw_lines = []
    for ln in text.splitlines():
        stripped = _strip_comment(ln).rstrip()
        if stripped.strip() == "":
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        raw_lines.append((indent, stripped.strip()))

    pos = 0

    def parse_block(min_indent: int):
        nonlocal pos
        if pos >= len(raw_lines):
            return None
        indent, content = raw_lines[pos]
        if content.startswith("- ") or content == "-":
            return parse_list(indent)
        return parse_map(indent)

    def parse_list(indent: int) -> List[Any]:
        nonlocal pos
        items: List[Any] = []
        while pos < len(raw_lines):
            cur_indent, content = raw_lines[pos]
            if cur_indent < indent or not (content.startswith("- ") or content == "-"):
                break
            body = content[1:].strip()
            pos += 1
            if body == "":
                items.append(parse_block(indent + 1))
            elif ":" in body and not body.startswith("\""):
                # inline first key of a mapping item
                key, _, val = body.partition(":")
                m: Dict[str, Any] = {}
                if val.strip() == "":
                    m[key.strip()] = parse_block(cur_indent + 2)
                else:
                    m[key.strip()] = _coerce(val)
                # consume sibling keys deeper-indented than the dash content
                while pos < len(raw_lines):
                    ni, nc = raw_lines[pos]
                    if ni <= cur_indent or nc.startswith("- "):
                        break
                    k2, _, v2 = nc.partition(":")
                    pos += 1
                    if v2.strip() == "":
                        m[k2.strip()] = parse_block(ni + 1)
                    else:
                        m[k2.strip()] = _coerce(v2)
                items.append(m)
            else:
                items.append(_coerce(body))
        return items

    def parse_map(indent: int) -> Dict[str, Any]:
        nonlocal pos
        m: Dict[str, Any] = {}
        while pos < len(raw_lines):
            cur_indent, content = raw_lines[pos]
            if cur_indent < indent or content.startswith("- "):
                break
            if cur_indent > indent:
                break
            key, sep, val = content.partition(":")
            if not sep:
                raise PlanError(f"Invalid line (expected 'key: value'): {content!r}")
            pos += 1
            if val.strip() == "":
                if pos < len(raw_lines) and raw_lines[pos][0] > cur_indent:
                    m[key.strip()] = parse_block(cur_indent + 1)
                else:
                    m[key.strip()] = None
            else:
                m[key.strip()] = _coerce(val)
        return m

    result = parse_block(0)
    return result if result is not None else {}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

# Terrain difficulty multipliers applied to baseline fuel burn.
TERRAIN_FACTORS = {
    "paved": 1.0,
    "gravel": 1.15,
    "dirt": 1.30,
    "sand": 1.60,
    "mountain": 1.75,
    "urban": 1.35,
    "mud": 1.90,
}


@dataclass
class Leg:
    name: str
    distance_km: float
    terrain: str = "paved"
    chokepoint: Optional[str] = None


@dataclass
class Chokepoint:
    name: str
    threat: float = 0.0       # 0..10 assessed threat level
    congestion: float = 0.0   # 0..10 traffic/passability congestion
    detour_km: float = 0.0    # km of available bypass (lower => higher risk)


@dataclass
class ConvoyPlan:
    mission: str
    vehicles: int
    fuel_burn_l_per_km: float          # per vehicle, baseline (paved)
    onboard_fuel_l: float              # per vehicle tank capacity
    load_factor: float = 1.0           # >1 increases burn (cargo/up-armor)
    reserve_pct: float = 0.10          # keep this fraction as safety reserve
    resupply_l: float = 0.0            # liters available at each resupply pt
    legs: List[Leg] = field(default_factory=list)
    chokepoints: List[Chokepoint] = field(default_factory=list)


@dataclass
class LegResult:
    name: str
    distance_km: float
    terrain: str
    terrain_factor: float
    fuel_burn_l: float            # total across all vehicles
    cumulative_km: float
    fuel_remaining_l: float       # per vehicle after this leg
    resupply_here: bool
    chokepoint: Optional[str]
    chokepoint_risk: Optional[float]


@dataclass
class PlanResult:
    mission: str
    vehicles: int
    total_distance_km: float
    total_fuel_l: float
    usable_fuel_per_vehicle_l: float
    resupply_count: int
    resupply_legs: List[str]
    max_chokepoint_risk: float
    feasible: bool
    warnings: List[str]
    legs: List[LegResult]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# Loading / validation
# ---------------------------------------------------------------------------

def parse_plan(text: str) -> ConvoyPlan:
    data = _parse_yaml(text)
    if not isinstance(data, dict):
        raise PlanError("Top-level plan must be a mapping.")

    def req(key: str) -> Any:
        if key not in data or data[key] is None:
            raise PlanError(f"Missing required field: {key!r}")
        return data[key]

    legs_raw = data.get("legs") or []
    if not isinstance(legs_raw, list) or not legs_raw:
        raise PlanError("Plan must define a non-empty 'legs' list.")

    def _req_float(mapping: dict, key: str, label: str) -> float:
        raw = mapping.get(key)
        if raw is None:
            raise PlanError(f"{label} missing or null for field {key!r}.")
        try:
            return float(raw)
        except (ValueError, TypeError):
            raise PlanError(
                f"{label} field {key!r} must be a number; got {raw!r}."
            )

    legs: List[Leg] = []
    for i, lr in enumerate(legs_raw):
        if not isinstance(lr, dict):
            raise PlanError(f"Leg #{i + 1} must be a mapping.")
        if "distance_km" not in lr:
            raise PlanError(f"Leg #{i + 1} missing 'distance_km'.")
        dist = _req_float(lr, "distance_km", f"Leg #{i + 1}")
        if dist <= 0:
            raise PlanError(f"Leg #{i + 1} distance_km must be > 0.")
        terrain = str(lr.get("terrain", "paved")).lower()
        if terrain not in TERRAIN_FACTORS:
            raise PlanError(
                f"Leg #{i + 1} unknown terrain {terrain!r}; "
                f"valid: {', '.join(sorted(TERRAIN_FACTORS))}"
            )
        legs.append(
            Leg(
                name=str(lr.get("name", f"leg-{i + 1}")),
                distance_km=dist,
                terrain=terrain,
                chokepoint=(str(lr["chokepoint"]) if lr.get("chokepoint") else None),
            )
        )

    cps_raw = data.get("chokepoints") or []
    chokepoints: List[Chokepoint] = []
    if cps_raw:
        if not isinstance(cps_raw, list):
            raise PlanError("'chokepoints' must be a list.")
        for j, cr in enumerate(cps_raw):
            if not isinstance(cr, dict) or "name" not in cr:
                raise PlanError("Each chokepoint needs a 'name'.")
            cp_label = f"Chokepoint #{j + 1} ({cr.get('name', '?')})"
            try:
                threat_val = float(cr.get("threat", 0.0))
                congestion_val = float(cr.get("congestion", 0.0))
                detour_val = float(cr.get("detour_km", 0.0))
            except (ValueError, TypeError) as exc:
                raise PlanError(
                    f"{cp_label} numeric field is not a valid number: {exc}"
                ) from exc
            chokepoints.append(
                Chokepoint(
                    name=str(cr["name"]),
                    threat=threat_val,
                    congestion=congestion_val,
                    detour_km=detour_val,
                )
            )

    vehicles_raw = req("vehicles")
    try:
        vehicles = int(vehicles_raw)
    except (ValueError, TypeError):
        raise PlanError(
            f"'vehicles' must be a whole number; got {vehicles_raw!r}."
        )
    if vehicles <= 0:
        raise PlanError("'vehicles' must be > 0.")

    burn_raw = req("fuel_burn_l_per_km")
    try:
        burn = float(burn_raw)
    except (ValueError, TypeError):
        raise PlanError(
            f"'fuel_burn_l_per_km' must be a number; got {burn_raw!r}."
        )
    if burn <= 0:
        raise PlanError("'fuel_burn_l_per_km' must be > 0.")

    onboard_raw = req("onboard_fuel_l")
    try:
        onboard = float(onboard_raw)
    except (ValueError, TypeError):
        raise PlanError(
            f"'onboard_fuel_l' must be a number; got {onboard_raw!r}."
        )
    if onboard <= 0:
        raise PlanError("'onboard_fuel_l' must be > 0.")

    reserve_raw = data.get("reserve_pct", 0.10)
    try:
        reserve = float(reserve_raw)
    except (ValueError, TypeError):
        raise PlanError(
            f"'reserve_pct' must be a number; got {reserve_raw!r}."
        )
    if not 0.0 <= reserve < 1.0:
        raise PlanError("'reserve_pct' must be in [0, 1).")

    load_raw = data.get("load_factor", 1.0)
    try:
        load = float(load_raw)
    except (ValueError, TypeError):
        raise PlanError(
            f"'load_factor' must be a number; got {load_raw!r}."
        )
    if load <= 0:
        raise PlanError("'load_factor' must be > 0.")

    resupply_raw = data.get("resupply_l", 0.0)
    try:
        resupply = float(resupply_raw)
    except (ValueError, TypeError):
        raise PlanError(
            f"'resupply_l' must be a number; got {resupply_raw!r}."
        )

    return ConvoyPlan(
        mission=str(data.get("mission", "unnamed")),
        vehicles=vehicles,
        fuel_burn_l_per_km=burn,
        onboard_fuel_l=onboard,
        load_factor=load,
        reserve_pct=reserve,
        resupply_l=resupply,
        legs=legs,
        chokepoints=chokepoints,
    )


def load_plan(path: str) -> ConvoyPlan:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return parse_plan(fh.read())
    except FileNotFoundError:
        raise
    except UnicodeDecodeError as exc:
        raise PlanError(
            f"Plan file {path!r} is not valid UTF-8 text (binary file?): {exc}"
        ) from exc
    except OSError as exc:
        raise PlanError(f"Could not read plan file {path!r}: {exc}") from exc


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def chokepoint_risk(cp: Chokepoint) -> float:
    """Bounded 0..100 risk score.

    Weighted blend of assessed threat and congestion, plus a detour
    penalty: a short/absent bypass concentrates the convoy and raises
    exposure. Monotonic and reproducible.
    """
    threat = max(0.0, min(10.0, cp.threat))
    congestion = max(0.0, min(10.0, cp.congestion))
    base = (threat * 7.0) + (congestion * 3.0)  # 0..100
    # Detour penalty: 0 km bypass => +20, decaying to ~0 by 50 km.
    detour_penalty = 20.0 * (1.0 / (1.0 + cp.detour_km / 10.0))
    return round(min(100.0, base + detour_penalty), 2)


def plan_convoy(plan: ConvoyPlan) -> PlanResult:
    cp_index = {c.name: c for c in plan.chokepoints}
    risk_index = {name: chokepoint_risk(c) for name, c in cp_index.items()}

    usable = plan.onboard_fuel_l * (1.0 - plan.reserve_pct)
    remaining = usable  # per-vehicle usable fuel above reserve
    cumulative_km = 0.0
    total_fuel = 0.0
    warnings: List[str] = []
    leg_results: List[LegResult] = []
    resupply_legs: List[str] = []
    feasible = True

    eff_burn = plan.fuel_burn_l_per_km * plan.load_factor

    for leg in plan.legs:
        factor = TERRAIN_FACTORS[leg.terrain]
        per_vehicle = leg.distance_km * eff_burn * factor
        leg_total = per_vehicle * plan.vehicles

        resupply_here = False
        # If this leg would exhaust usable fuel, attempt resupply BEFORE it.
        if per_vehicle > remaining:
            if plan.resupply_l > 0:
                # Top up to usable capacity (resupply_l caps the top-up).
                topup = min(plan.resupply_l, usable - remaining)
                remaining += topup
                resupply_here = True
                resupply_legs.append(leg.name)
            if per_vehicle > remaining:
                feasible = False
                warnings.append(
                    f"INFEASIBLE: leg '{leg.name}' needs {per_vehicle:.1f} L/vehicle "
                    f"but only {remaining:.1f} L usable available."
                )

        remaining = max(0.0, remaining - per_vehicle)
        cumulative_km += leg.distance_km
        total_fuel += leg_total

        cp_risk = None
        if leg.chokepoint:
            if leg.chokepoint not in risk_index:
                warnings.append(
                    f"Leg '{leg.name}' references undefined chokepoint "
                    f"'{leg.chokepoint}'."
                )
            else:
                cp_risk = risk_index[leg.chokepoint]
                if cp_risk >= 70.0:
                    warnings.append(
                        f"HIGH RISK ({cp_risk:.0f}/100) at chokepoint "
                        f"'{leg.chokepoint}' on leg '{leg.name}'."
                    )

        leg_results.append(
            LegResult(
                name=leg.name,
                distance_km=leg.distance_km,
                terrain=leg.terrain,
                terrain_factor=factor,
                fuel_burn_l=round(leg_total, 2),
                cumulative_km=round(cumulative_km, 2),
                fuel_remaining_l=round(remaining, 2),
                resupply_here=resupply_here,
                chokepoint=leg.chokepoint,
                chokepoint_risk=cp_risk,
            )
        )

    max_risk = max(risk_index.values(), default=0.0)

    return PlanResult(
        mission=plan.mission,
        vehicles=plan.vehicles,
        total_distance_km=round(cumulative_km, 2),
        total_fuel_l=round(total_fuel, 2),
        usable_fuel_per_vehicle_l=round(usable, 2),
        resupply_count=len(resupply_legs),
        resupply_legs=resupply_legs,
        max_chokepoint_risk=round(max_risk, 2),
        feasible=feasible,
        warnings=warnings,
        legs=leg_results,
    )
