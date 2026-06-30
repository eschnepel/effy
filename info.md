# Effy – Effective PV Loss Distribution

Calculates conversion/wiring losses across a PV + BMS system and distributes
them absolutely evenly across all active input sources using a waterfall
model.

## Features

- Works with W, kW, Wh, or kWh sensors in any mix
- One `effy_*` output sensor per configured input sensor
- Diagnostic button to recalculate up to N days of 5-minute history
- Fully event-driven, debounced live updates via a shared coordinator

See the [README](https://github.com/your-repo/effy/blob/main/README.md) for
configuration details and a full worked example.
