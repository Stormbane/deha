"""Config flow for Narada."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import CONF_BRAIN_URL, DEFAULT_BRAIN_URL, DOMAIN


class NaradaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Narada."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="Narada", data=user_input)

        schema = vol.Schema({
            vol.Required(CONF_BRAIN_URL, default=DEFAULT_BRAIN_URL): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema)
