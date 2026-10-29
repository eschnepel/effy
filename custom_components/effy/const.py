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

# The battery efficiency's own options (ADR-023 decision 1), all optional: the state of charge,
# the battery capacity and the valley hysteresis. SoC and capacity are neither inputs nor
# outputs of the waterfall; they exist only to measure the battery's own efficiency.
CONF_SOC_SENSOR = "soc_sensor"
CONF_CAPACITY_ENTITY = "capacity_entity"
CONF_VALLEY_HYSTERESIS = "valley_hysteresis"

DEFAULT_MAX_HISTORY_DAYS = 28
# The valley hysteresis in SoC points. The minimum keeps it above the 10-point valley band
# (ADR-023 decision 6), so a valley can end before the rise that makes it real.
DEFAULT_VALLEY_HYSTERESIS = 30
MIN_VALLEY_HYSTERESIS = 15
# The capacity entity reports energy in one of these units, any other is an error (decision 1).
CAPACITY_UNITS = ("Wh", "kWh")

# The published contract (schema version, aggregations, entity IDs, keys) lives in
# effy_exports.py, a standalone file other projects copy (ADR-021 Amendment 2026-10-23).
