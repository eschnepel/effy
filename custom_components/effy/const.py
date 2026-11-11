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

# The EPS/UPS port of the inverter (ADR-026): one optional signed power sensor, positive while
# energy leaves through the port, negative while it enters (another inverter feeds it). Neither
# a PV input nor a BMS counter; per slot it is split into an EPS output and an EPS input.
CONF_EPS_SENSOR = "eps_sensor"

# The battery efficiency's own options (ADR-023 decision 1), all optional: the state of charge,
# the battery capacity and the valley hysteresis. SoC and capacity are neither inputs nor
# outputs of the waterfall; they exist only to measure the battery's own efficiency.
CONF_SOC_SENSOR = "soc_sensor"
CONF_CAPACITY_ENTITY = "capacity_entity"
CONF_VALLEY_HYSTERESIS = "valley_hysteresis"

# Write a debug export file with every history recalculation of the button (TASK-0037,
# ADR-025 Amendment 2026-11-03). Off unless set.
CONF_DEBUG_EXPORT = "debug_export"

DEFAULT_MAX_HISTORY_DAYS = 28
# The valley hysteresis in SoC points. The minimum keeps it above the 10-point valley band
# (ADR-023 decision 6), so a valley can end before the rise that makes it real.
DEFAULT_VALLEY_HYSTERESIS = 30
MIN_VALLEY_HYSTERESIS = 15
# The capacity entity reports energy in one of these units, any other is an error (decision 1).
CAPACITY_UNITS = ("Wh", "kWh")
# The EPS sensor reports power in one of these units, any other is an error (ADR-026 decision 1).
EPS_UNITS = ("W", "kW")

# The PV combined series (ADR-027 decision 1, TASK-0045a): the effective values of all PV
# inputs added up per slot, in W. Its statistic ID is the entity ID of the sensor "Effective PV
# Sum now"; the series is written into ``per_sensor`` like any effective series. Not yet in the
# published contract: TASK-0045f moves these literals into ``effy_exports.py``.
PV_SUM_ENTITY_ID = "sensor.effy_effective_pv_sum_now"
PV_SUM_UNIQUE_ID_SUFFIX = "effective_pv_sum_now"
PV_SUM_TRANSLATION_KEY = "effective_pv_sum_now"

# The PV delta integrals (ADR-027 decision 3, TASK-0045c): a per-input one,
# ``sensor.effy_<slug>_delta_integral_today``, and the combined *today* and *total* ones.
PV_DELTA_INTEGRAL_TODAY_SUFFIX = "_delta_integral_today"
PV_DELTA_TODAY_ENTITY_ID = "sensor.effy_pv_delta_integral_today"
PV_DELTA_TOTAL_ENTITY_ID = "sensor.effy_pv_delta_integral_total"

# The published contract (schema version, aggregations, entity IDs, keys) lives in
# effy_exports.py, a standalone file other projects copy (ADR-021 Amendment 2026-10-23).
