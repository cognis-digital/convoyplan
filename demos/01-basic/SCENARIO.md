# Demo 01 - Basic sustainment run

A four-vehicle resupply convoy moving from a forward logistics base to a
distribution point across mixed terrain, passing two chokepoints.

This demonstrates CONVOYPLAN's core analytical outputs:

- **Per-leg fuel burn** scaled by terrain difficulty and load factor.
- **Resupply windows**: the planner detects when usable fuel (tank minus
  the 12% safety reserve) would be exhausted and consumes a resupply
  point before the offending leg.
- **Chokepoint risk**: a bounded 0..100 score blending assessed threat,
  congestion, and the length of any available bypass (shorter detour =
  higher concentration risk).

## Run it

```bash
python -m convoyplan plan demos/01-basic/convoy.yaml
python -m convoyplan plan demos/01-basic/convoy.yaml --format json
```

Exit code is `0` when the plan is sustainable and `1` when it is
infeasible (a leg cannot be completed even after resupply). This makes
it usable as a gate in a CI / planning pipeline.

## Try a failure

Drop `onboard_fuel_l` or `resupply_l` to a small value and the planner
will flag the leg that runs the convoy dry and exit non-zero.

This is a planning / compliance aid only. It contains no targeting,
weapon-control, or offensive capability.
