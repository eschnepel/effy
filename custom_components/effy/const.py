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

# Configuration published as attributes of sensor.effy_recalculated_from
# (ADR-021). Raised only for a change that breaks existing keys or meanings.
PUBLISHED_SCHEMA_VERSION = 1

# The aggregations offered by the short-term statistics Effy writes: every row
# carries exactly ``start``, ``mean`` and ``state`` and never min/max/sum
# (history._write_recorder_statistics, ADR-003, TASK-0007a). A test ties this
# constant to the writer, so the two cannot drift.
EFFY_AGGREGATIONS = ("mean", "state")

# The standby power sensor is not written by Effy's statistics writer; its
# short-term statistics are the recorder's own, compiled from a measurement
# sensor (ADR-018, ADR-020).
STANDBY_AGGREGATIONS = ("mean", "min", "max")
