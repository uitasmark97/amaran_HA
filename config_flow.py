import logging
from typing import Any, Dict, Optional

import voluptuous as vol

# 条件导入HomeAssistant模块
try:
    from homeassistant import config_entries
    from homeassistant.data_entry_flow import FlowResult
    HAS_HOMEASSISTANT = True
except ImportError:
    HAS_HOMEASSISTANT = False

# 从本地const.py导入常量
from .const import DOMAIN, CONF_HOST, CONF_PORT, CONF_API_KEY
from . import AmaranAPI

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_HOST): str,
    vol.Required(CONF_PORT, default=12345): int,
    vol.Required(CONF_API_KEY): str,
})

# 仅在HomeAssistant环境中定义ConfigFlow类
if HAS_HOMEASSISTANT:
    class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
        """Handle a config flow for Amaran."""
        VERSION = 1
        CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

        async def async_step_user(
            self, user_input: Optional[Dict[str, Any]] = None
        ) -> FlowResult:
            """Handle the initial step."""
            errors: Dict[str, str] = {}

            if user_input is not None:
                # 验证连接
                api = AmaranAPI(self.hass, user_input[CONF_HOST], user_input[CONF_PORT], user_input[CONF_API_KEY])
                connected = await api.connect()
                if connected:
                    await api.close()
                    return self.async_create_entry(title="Amaran Lights", data=user_input)
                else:
                    errors["base"] = "cannot_connect"

            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
            )