"""Constants for the Effy integration."""

DOMAIN = "effy"

# Inputs and outputs are configured by role. PV inputs are a list (ADR-019); the
# four BMS counters are one optional sensor each (ADR-023 decision 1): grid import
# and discharge are BMS inputs, charge and grid export are BMS outputs. The
# waterfall (ADR-001) still sees one mixed pool of inputs and the outputs; the role
# matters to the standby power sensor (ADR-018) and the efficiency sensors
# (ADR-023). The old ``bms_input_sensors`` / ``output_sensors`` keys are read nowhere
# (no migration, ADR-023 decision 1); the pre-ADR-019 key survives only as the
# migration literal in inputs.py.
CONF_PV_INPUT_SENSORS = "pv_input_sensors"
CONF_GRID_IMPORT_SENSOR = "grid_import_sensor"
CONF_DISCHARGE_SENSOR = "discharge_sensor"
CONF_CHARGE_SENSOR = "charge_sensor"
CONF_GRID_EXPORT_SENSOR = "grid_export_sensor"
CONF_MAX_HISTORY_DAYS = "max_history_days"

DEFAULT_MAX_HISTORY_DAYS = 28

# The published contract (schema version, aggregations, entity IDs, keys) lives in
# effy_exports.py, a standalone file other projects copy (ADR-021 Amendment 2026-10-23).
