"""Config flow for Effy integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_INPUT_SENSORS,
    CONF_MAX_HISTORY_DAYS,
    CONF_OUTPUT_SENSORS,
    DEFAULT_MAX_HISTORY_DAYS,
    DOMAIN,
)


def _build_schema(
    input_sensors: list[str] | None = None,
    output_sensors: list[str] | None = None,
    max_history_days: int = DEFAULT_MAX_HISTORY_DAYS,
) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_INPUT_SENSORS,
                default=input_sensors or [],
            ): selector.selector({"entity": {"multiple": True, "domain": ["sensor"]}}),
            vol.Required(
                CONF_OUTPUT_SENSORS,
                default=output_sensors or [],
            ): selector.selector({"entity": {"multiple": True, "domain": ["sensor"]}}),
            vol.Optional(
                CONF_MAX_HISTORY_DAYS,
                default=max_history_days,
            ): selector.selector(
                {
                    "number": {
                        "min": 1,
                        "max": 365,
                        "step": 1,
                        "mode": "box",
                        "unit_of_measurement": "days",
                    }
                }
            ),
        }
    )


class EffyConfigFlow(
    config_entries.ConfigFlow,  # type: ignore[misc]
    domain=DOMAIN,
):
    """Handle a config flow for Effy."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.FlowResult:
        """Handle the initial step.

        Validation failures return translatable error keys (resolved via
        translations/*.json), never raw text — see ADR-000 §8.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            if len(user_input.get(CONF_INPUT_SENSORS, [])) < 1:
                errors[CONF_INPUT_SENSORS] = "at_least_one_input"
            elif len(user_input.get(CONF_OUTPUT_SENSORS, [])) < 1:
                errors[CONF_OUTPUT_SENSORS] = "at_least_one_output"
            else:
                return self.async_create_entry(
                    title="Effy",
                    data={},
                    options=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_build_schema(),
            errors=errors,
        )

    @staticmethod
    @callback  # type: ignore
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> EffyOptionsFlow:
        """Return the options flow handler."""
        return EffyOptionsFlow(config_entry)


class EffyOptionsFlow(config_entries.OptionsFlow):  # type: ignore[misc]
    """Handle options for Effy."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.FlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if len(user_input.get(CONF_INPUT_SENSORS, [])) < 1:
                errors[CONF_INPUT_SENSORS] = "at_least_one_input"
            elif len(user_input.get(CONF_OUTPUT_SENSORS, [])) < 1:
                errors[CONF_OUTPUT_SENSORS] = "at_least_one_output"
            else:
                return self.async_create_entry(title="", data=user_input)

        current = self._config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=_build_schema(
                input_sensors=current.get(CONF_INPUT_SENSORS, []),
                output_sensors=current.get(CONF_OUTPUT_SENSORS, []),
                max_history_days=current.get(CONF_MAX_HISTORY_DAYS, DEFAULT_MAX_HISTORY_DAYS),
            ),
            errors=errors,
        )
