"""Constants for the Effy integration."""

DOMAIN = "effy"

CONF_INPUT_SENSORS = "input_sensors"
CONF_OUTPUT_SENSORS = "output_sensors"
CONF_MAX_HISTORY_DAYS = "max_history_days"

DEFAULT_MAX_HISTORY_DAYS = 28

# Every entity this integration creates is named "sensor.effy_*" (see
# sensor.py: EffySensor/EffyDerivedPowerSensor/EffySmoothedSensor/
# EffyRecalculatedFromSensor). ADR-017 requires users to exclude this glob
# from the recorder's own state/statistics recording, since history.py
# already writes their statistics directly via the recorder's internal
# async_import_statistics API (ADR-003) — leaving them recorder-tracked
# makes HA's own native statistics compiler redundantly (and, per ADR-017,
# sometimes conflictingly) compile the same slots a second time.
RECORDER_EXCLUDE_ENTITY_GLOB = "sensor.effy_*"

# Synthetic, never-created entity_id used only to probe whether the glob
# above is actually excluded (recorder.entity_filter is a pure name-pattern
# test — it does not require the entity to exist — see ADR-017).
RECORDER_EXCLUSION_PROBE_ENTITY_ID = "sensor.effy_recorder_exclusion_probe"

ISSUE_RECORDER_NOT_EXCLUDED = "recorder_not_excluded"
