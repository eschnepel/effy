"""Constants for the Effy integration."""

DOMAIN = "effy"

# Input sensors are configured by role (ADR-019): PV inputs and BMS inputs. The
# waterfall (ADR-001) treats both as one mixed pool; the role only matters to
# the standby power sensor (ADR-018). The pre-ADR-019 single key survives only
# as the migration literal in inputs.py.
CONF_PV_INPUT_SENSORS = "pv_input_sensors"
CONF_BMS_INPUT_SENSORS = "bms_input_sensors"
CONF_OUTPUT_SENSORS = "output_sensors"
CONF_MAX_HISTORY_DAYS = "max_history_days"

DEFAULT_MAX_HISTORY_DAYS = 28

# The published contract (schema version, aggregations, entity IDs, keys) lives in
# effy_exports.py, a standalone file other projects copy (ADR-021 Amendment 2026-10-23).
