import asyncio
import json
import logging
import time
import os
import base64
from typing import Optional, Dict, Any

import voluptuous as vol

# 条件导入HomeAssistant模块
try:
    from homeassistant.core import HomeAssistant
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.helpers import discovery
    from homeassistant.helpers.typing import ConfigType
    HAS_HOMEASSISTANT = True
except ImportError:
    HAS_HOMEASSISTANT = False

# 从本地const.py导入常量
from .const import DOMAIN, PLATFORMS, CONF_HOST, CONF_PORT, CONF_API_KEY

_LOGGER = logging.getLogger(__name__)

# 简化的配置schema，用于测试环境
CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT): int,
        vol.Required(CONF_API_KEY): str,
    })
}, extra=vol.ALLOW_EXTRA)

from .const import DOMAIN, PLATFORMS

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT): int,
        vol.Required(CONF_API_KEY): str,
    })
}, extra=vol.ALLOW_EXTRA)

async def async_setup(hass, config) -> bool:
    """Set up the Amaran component."""
    if DOMAIN not in config:
        return True

    conf = config[DOMAIN]
    hass.data[DOMAIN] = {
        'host': conf[CONF_HOST],
        'port': conf[CONF_PORT],
        'api_key': conf[CONF_API_KEY],
        'websocket': None,
        'devices': {},
        'scenes': {},
        'quickshots': {},
        'presets': {}
    }

    # 仅在HomeAssistant环境中执行发现和注册
    if HAS_HOMEASSISTANT:
        # 发现设备并注册平台
        for platform in PLATFORMS:
            hass.async_create_task(
                discovery.async_load_platform(
                    hass, platform, DOMAIN, {}, config
                )
            )

    return True

async def async_setup_entry(hass, entry) -> bool:
    """Set up Amaran from a config entry."""
    _LOGGER.debug("Starting async_setup_entry for Amaran integration")
    # 初始化数据存储
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {
            'host': entry.data[CONF_HOST],
            'port': entry.data[CONF_PORT],
            'api_key': entry.data[CONF_API_KEY],
            'websocket': None,
            'devices': {},
            'scenes': {},
            'quickshots': {},
            'presets': {}
        }
        _LOGGER.debug("Initialized data storage for Amaran integration")
    else:
        # 更新现有配置
        hass.data[DOMAIN]['host'] = entry.data[CONF_HOST]
        hass.data[DOMAIN]['port'] = entry.data[CONF_PORT]
        hass.data[DOMAIN]['api_key'] = entry.data[CONF_API_KEY]
        _LOGGER.debug("Updated existing configuration for Amaran integration")

    # 创建API实例
    _LOGGER.debug("Creating AmaranAPI instance")
    try:
        api = AmaranAPI(hass, hass.data[DOMAIN]['host'], hass.data[DOMAIN]['port'], hass.data[DOMAIN]['api_key'])
        hass.data[DOMAIN]['api'] = api
        _LOGGER.debug("AmaranAPI instance created successfully")
        # 尝试连接
        _LOGGER.debug("Attempting to connect to Amaran device")
        connected = await api.connect()
        if not connected:
            _LOGGER.error("Failed to connect to Amaran device")
            return False
        _LOGGER.debug("Successfully connected to Amaran device")
    except Exception as e:
        _LOGGER.error(f"Failed to initialize Amaran API: {e}")
        return False

    # 注册设置预设服务
    async def async_set_preset(service):
        """设置预设."""
        try:
            preset_id = service.data['preset_id']
            device_id = service.data['device_id']
        except KeyError as e:
            _LOGGER.error(f"缺少必需参数: {e}")
            return

        data = hass.data[DOMAIN]
        # 检查设备是否存在
        if device_id not in data['devices']:
            _LOGGER.error(f"设备ID {device_id} 不存在")
            return

        device = data['devices'][device_id]
        await data['api'].send_request("set_preset", node_id=device._node_id, args={"preset_id": preset_id})

    hass.services.async_register(
        DOMAIN,
        "set_preset",
        async_set_preset,
        vol.Schema({
            vol.Required('preset_id'): str,
            vol.Required('device_id'): str
        })
    )

    # 注册设置快照服务
    async def async_set_quickshot(service):
        """设置快照."""
        try:
            quickshot_id = service.data['quickshot_id']
            device_id = service.data['device_id']
        except KeyError as e:
            _LOGGER.error(f"缺少必需参数: {e}")
            return

        data = hass.data[DOMAIN]
        # 检查设备是否存在
        if device_id not in data['devices']:
            _LOGGER.error(f"设备ID {device_id} 不存在")
            return

        device = data['devices'][device_id]
        await data['api'].send_request("set_quickshot", node_id=device._node_id, args={"quickshot_id": quickshot_id})

    # 获取可用的快照ID列表
    async def get_available_quickshots(hass, device_id=None):
        """获取可用的快照ID列表"""
        data = hass.data[DOMAIN]
        try:
            # 从API获取实际的快照列表
            quickshot_list = await data['api'].get_quickshot_list()
            if quickshot_list and 'data' in quickshot_list:
                return [{'value': str(qs.get('id')), 'label': qs.get('name', f"快照 {qs.get('id')}")} for qs in quickshot_list['data']]
            else:
                _LOGGER.warning("获取快照列表失败，返回空列表")
                return []
        except Exception as e:
            _LOGGER.error(f"获取快照列表时发生错误: {e}")
            # 发生错误时返回空列表
            return []

    hass.services.async_register(
        DOMAIN,
        "set_quickshot",
        async_set_quickshot,
        vol.Schema({
            vol.Required('quickshot_id'): vol.All(
                str,
                vol.In([item['value'] for item in await get_available_quickshots(hass)])
            ),
            vol.Required('device_id'): str
        },
        extra=vol.ALLOW_EXTRA
        )
    )

    # 为服务添加描述，以便UI可以显示选择界面
    hass.data[DOMAIN]['services'] = {
        'set_quickshot': {
            'quickshot_id': {
                'name': '快照ID',
                'description': '选择要应用的快照',
                'selector': {
                    'select': {
                        'options': await get_available_quickshots(hass)
                    }
                }
            },
            'device_id': {
                'name': '设备ID',
                'description': '选择要应用快照的设备'
            }
        }
    }

    # 发现设备
    try:
        device_list = await api.get_device_list()
        _LOGGER.debug(f"Device list API response: {device_list}")
        if device_list and 'data' in device_list:
            hass.data[DOMAIN]['devices'] = {}
            for device in device_list['data']:
                device_id = device.get('device_id')
                if device_id:
                    hass.data[DOMAIN]['devices'][device_id] = device
                    _LOGGER.debug(f"Discovered device: {device_id}, details: {device}")
            _LOGGER.info(f"Discovered {len(hass.data[DOMAIN]['devices'])} Amaran devices")
        else:
            _LOGGER.warning("No Amaran devices discovered")
    except Exception as e:
        _LOGGER.error(f"Failed to discover devices: {e}", exc_info=True)

    # 转发配置到平台
    _LOGGER.debug("Forwarding config entry setups to platforms")
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        _LOGGER.info("Amaran integration setup completed successfully")
        return True
    except Exception as e:
        _LOGGER.error(f"Failed to forward entry setups: {e}")
        return False

async def async_unload_entry(hass, entry) -> bool:
    """Unload a config entry."""
    # 仅在HomeAssistant环境中执行卸载
    if HAS_HOMEASSISTANT:
        unload_ok = all(
            await asyncio.gather(
                *[hass.config_entries.async_forward_entry_unload(entry, platform) for platform in PLATFORMS]
            )
        )

        if unload_ok:
            hass.data.pop(DOMAIN)

        return unload_ok
    return True

class AmaranAPI:
    """Amaran API client."""
    def __init__(self, hass, host: str, port: int, api_key: str):
        # 在测试环境中，hass可以是一个简单的字典或模拟对象
        self.hass = hass
        self.host = host
        self.port = port
        self.api_key = api_key
        self.websocket = None
        self.client_id = 1
        self.request_id = 1
        self._ws_lock = asyncio.Lock()
        self._last_request_time = 0
        self._min_request_interval = 0.2  # 200ms

    async def connect(self) -> bool:
        """Connect to the Amaran WebSocket server."""
        try:
            import websockets
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.backends import default_backend

            uri = f"ws://{self.host}:{self.port}"
            self.websocket = await websockets.connect(uri)
            _LOGGER.info("Connected to Amaran WebSocket server")
            return True
        except Exception as e:
            _LOGGER.error(f"Failed to connect to Amaran WebSocket server: {e}")
            return False

    def generate_token(self) -> str:
        """Generate token using AES-256-GCM."""
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.backends import default_backend

            iv = os.urandom(12)
            encryptor = Cipher(
                algorithms.AES(base64.b64decode(self.api_key)),
                modes.GCM(iv),
                backend=default_backend()
            ).encryptor()
            now = int(time.time())
            ciphertext = encryptor.update(str(now).encode()) + encryptor.finalize()
            combined = iv + encryptor.tag + ciphertext
            return base64.b64encode(combined).decode()
        except Exception as e:
            _LOGGER.error(f"Failed to generate token: {e}")
            return ""

    async def send_request(self, action: str, node_id: Optional[str] = None, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Send request to Amaran WebSocket server."""
        # 检查请求间隔
        current_time = time.time()
        elapsed = current_time - self._last_request_time
        if elapsed < self._min_request_interval:
            await asyncio.sleep(self._min_request_interval - elapsed)
        self._last_request_time = time.time()

        if not self.websocket:
            if not await self.connect():
                return {}

        token = self.generate_token()
        if not token:
            return {}

        request = {
            "version": 2,
            "type": "request",
            "client_id": self.client_id,
            "request_id": self.request_id,
            "action": action,
            "token": token
        }

        if node_id:
            request["node_id"] = node_id

        if args:
            request["args"] = args

        try:
            async with self._ws_lock:
                await self.websocket.send(json.dumps(request))
                self.request_id += 1
                response = await self.websocket.recv()
                return json.loads(response)
        except Exception as e:
            _LOGGER.error(f"Failed to send request: {e}")
            self.websocket = None
            return {}

    async def get_device_list(self) -> Dict[str, Any]:
        """Get list of devices."""
        return await self.send_request("get_device_list")

    async def get_node_config(self, node_id: str) -> Dict[str, Any]:
        """Get node configuration."""
        return await self.send_request("get_node_config", node_id=node_id)

    async def get_quickshot_list(self) -> Dict[str, Any]:
        """Get list of quickshots."""
        return await self.send_request("get_quickshot_list")

    async def get_preset_list(self) -> Dict[str, Any]:
        """Get list of presets."""
        return await self.send_request("get_preset_list")

    async def close(self) -> None:
        """Close the WebSocket connection."""
        if self.websocket:
            await self.websocket.close()
            self.websocket = None