import asyncio
import logging
from typing import Optional, Dict, Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.color import (
    color_temperature_kelvin_to_mired,
    color_temperature_mired_to_kelvin
)

from .const import DOMAIN
from . import AmaranAPI

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Amaran light entities from a config entry."""
    data = hass.data[DOMAIN]
    api = AmaranAPI(hass, data['host'], data['port'], data['api_key'])

    # 获取设备列表
    device_list = await api.get_device_list()
    if not device_list or 'data' not in device_list:
        _LOGGER.error("Failed to get device list")
        return

    # 获取快照和预设列表
    quickshot_list = await api.get_quickshot_list()
    if quickshot_list and 'data' in quickshot_list:
        data['quickshots'] = {item['id']: item['name'] for item in quickshot_list['data']}

    preset_list = await api.get_preset_list()
    if preset_list and 'data' in preset_list:
        data['presets'] = preset_list['data']

    # 创建灯光实体
    lights = []
    for device in device_list['data']:
        # 跳过群组
        if device['id'] == '00000000000000000000000000000000':
            continue

        # 获取设备配置
        node_config = await api.get_node_config(device['node_id'])
        if not node_config or 'data' not in node_config:
            _LOGGER.error(f"Failed to get node config for {device['name']}")
            continue

        config = node_config['data']
        # 初始化支持的颜色模式集合
        color_modes = {ColorMode.ONOFF, ColorMode.BRIGHTNESS}

        # 检查支持的功能
        if config.get('cct_support', False):
            color_modes.add(ColorMode.COLOR_TEMP)

        if config.get('rgb_support', False) or config.get('hsi_support', False):
            color_modes.add(ColorMode.HS)
            color_modes.add(ColorMode.RGB)

        # 保留为set以便后续处理
        color_modes = set(color_modes)

        # 创建灯光实体
        light = AmaranLight(
            api, 
            device['id'], 
            device['name'], 
            device['node_id'], 
            color_modes, 
            config
        )
        lights.append(light)
        data['devices'][device['id']] = light

    async_add_entities(lights, update_before_add=True)

class AmaranLight(LightEntity):
    """Representation of an Amaran light."""
    def __init__(
        self, 
        api: AmaranAPI, 
        device_id: str, 
        name: str, 
        node_id: str, 
        color_modes: set[ColorMode], 
        config: Dict[str, Any]
    ):
        self._api = api
        self._device_id = device_id
        self._name = name
        self._node_id = node_id
        # 修复颜色模式设置，确保只包含有效的非互斥模式
        # 移除ONOFF因为BRIGHTNESS已包含此功能
        self._color_modes = set(color_modes)
        if ColorMode.ONOFF in self._color_modes:
            self._color_modes.remove(ColorMode.ONOFF)
        
        # 确保BRIGHTNESS不与其他颜色模式共存
        if (ColorMode.COLOR_TEMP in self._color_modes or 
            ColorMode.HS in self._color_modes or 
            ColorMode.RGB in self._color_modes):
            if ColorMode.BRIGHTNESS in self._color_modes:
                self._color_modes.remove(ColorMode.BRIGHTNESS)
        
        # 定义主要颜色模式
        primary_modes = []
        if ColorMode.COLOR_TEMP in self._color_modes:
            primary_modes.append(ColorMode.COLOR_TEMP)
        if ColorMode.HS in self._color_modes:
            primary_modes.append(ColorMode.HS)
        if ColorMode.RGB in self._color_modes:
            primary_modes.append(ColorMode.RGB)
        
        # 移除互斥的颜色模式组合
        # 确保COLOR_TEMP、HS和RGB不会同时存在
        if len(primary_modes) > 1:
            # 优先级: COLOR_TEMP > HS > RGB
            self._color_modes = set(self._color_modes)
            for mode in primary_modes:
                if mode != ColorMode.COLOR_TEMP and ColorMode.COLOR_TEMP in primary_modes:
                    self._color_modes.remove(mode)
                elif mode != ColorMode.HS and ColorMode.HS in primary_modes and ColorMode.COLOR_TEMP not in primary_modes:
                    self._color_modes.remove(mode)
        
        self._attr_supported_color_modes = self._color_modes
        
        # 确定默认颜色模式
        if ColorMode.COLOR_TEMP in self._color_modes:
            self._color_mode = ColorMode.COLOR_TEMP
        elif ColorMode.HS in self._color_modes:
            self._color_mode = ColorMode.HS
        elif ColorMode.RGB in self._color_modes:
            self._color_mode = ColorMode.RGB
        else:
            self._color_mode = ColorMode.BRIGHTNESS
        self._config = config
        self._is_on = False
        self._brightness = 255
        self._color_temp = None
        self._hs_color = None
        self._rgb_color = None
        self._cct_min = config.get('cct_min', 2000)
        self._cct_max = config.get('cct_max', 10000)

    @property
    def name(self) -> str:
        """Return the name of the light."""
        return self._name

    @property
    def unique_id(self) -> str:
        """Return the unique ID of the light."""
        return self._device_id

    @property
    def is_on(self) -> bool:
        """Return true if the light is on."""
        return self._is_on

    @property
    def brightness(self) -> int:
        """Return the brightness of the light."""
        return self._brightness

    @property
    def color_temp(self) -> int:
        """Return the color temperature of the light in mireds."""
        if self._color_temp is not None and self._color_temp > 0:
            return color_temperature_kelvin_to_mired(self._color_temp)
        return None

    @property
    def color_temp_kelvin(self) -> int:
        """Return the color temperature of the light in kelvin."""
        return self._color_temp if self._color_temp is not None and self._color_temp > 0 else None

    @property
    def min_color_temp_kelvin(self) -> int:
        """Return the minimum color temperature in kelvin."""
        return self._cct_min

    @property
    def max_color_temp_kelvin(self) -> int:
        """Return the maximum color temperature in kelvin."""
        return self._cct_max

    @property
    def min_mireds(self) -> int:
        """Return the minimum color temperature in mireds."""
        return color_temperature_kelvin_to_mired(self._cct_max)

    @property
    def max_mireds(self) -> int:
        """Return the maximum color temperature in mireds."""
        return color_temperature_kelvin_to_mired(self._cct_min)

    @property
    def hs_color(self) -> tuple:
        """Return the HS color value."""
        return self._hs_color

    @property
    def rgb_color(self) -> tuple:
        """Return the RGB color value."""
        return self._rgb_color

    @property
    def color_mode(self) -> ColorMode:
        """Return the current color mode."""
        return self._color_mode

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return extra state attributes."""
        # 获取全局快照和预设列表
        hass = self.hass
        domain_data = hass.data.get(DOMAIN, {})
        quickshots = domain_data.get('quickshots', {})
        presets = domain_data.get('presets', [])
        
        # 格式化快照信息为ID:名称
        quickshot_info = {str(id): name for id, name in quickshots.items()}
        
        # 格式化预设信息为ID:名称
        preset_info = {}
        for preset in presets:
            if isinstance(preset, dict):
                preset_id = str(preset.get('id'))
                preset_name = preset.get('name')
                if preset_id and preset_name:
                    preset_info[preset_id] = preset_name
        
        return {
            'device_id': self._device_id,
            'node_id': self._node_id,
            'cct_min': self._cct_min,
            'cct_max': self._cct_max,
            'quickshot_ids': quickshot_info,
            'preset_ids': preset_info
        }

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on the light."""
        _LOGGER.debug(f"Turning on device {self._name} with kwargs: {kwargs}")
        # 定义颜色模式优先级: COLOR_TEMP > HS > RGB
        # 初始化颜色模式变量
        new_color_mode = None
        intensity = int(self._brightness * 1000 / 255) if self._brightness else 1000
        _LOGGER.debug(f"Initial intensity: {intensity}, current brightness: {self._brightness}")

        # 亮度
        if ATTR_BRIGHTNESS in kwargs:
            brightness = kwargs[ATTR_BRIGHTNESS]
            # 转换为 0-1000 范围
            intensity = int(brightness * 1000 / 255)
            await self._api.send_request(
                "set_intensity", 
                node_id=self._node_id, 
                args={"intensity": intensity}
            )
            self._brightness = brightness

        # 色温 (最高优先级)
        if ATTR_COLOR_TEMP_KELVIN in kwargs and new_color_mode is None:
            kelvin = kwargs[ATTR_COLOR_TEMP_KELVIN]
            # 确保在设备支持的范围内
            kelvin = max(self._cct_min, min(self._cct_max, kelvin))
            await self._api.send_request(
                "set_cct", 
                node_id=self._node_id, 
                args={"cct": kelvin}
            )
            self._color_temp = kelvin
            new_color_mode = ColorMode.COLOR_TEMP
            # 清除其他颜色模式
            self._hs_color = None
            self._rgb_color = None

        # HS 颜色 (次高优先级)
        if ATTR_HS_COLOR in kwargs and new_color_mode is None:
            hs_color = kwargs[ATTR_HS_COLOR]
            hue, sat = hs_color
            # 转换为整数
            hue = int(hue)
            sat = int(sat)
            await self._api.send_request(
                "set_hsi", 
                node_id=self._node_id, 
                args={
                    "hue": hue, 
                    "sat": sat, 
                    "intensity": intensity
                }
            )
            self._hs_color = hs_color
            new_color_mode = ColorMode.HS
            # 清除其他颜色模式
            self._color_temp = None
            self._rgb_color = None

        # RGB 颜色 (最低优先级)
        if ATTR_RGB_COLOR in kwargs and new_color_mode is None:
            rgb_color = kwargs[ATTR_RGB_COLOR]
            r, g, b = rgb_color
            await self._api.send_request(
                "set_rgb", 
                node_id=self._node_id, 
                args={
                    "r": r, 
                    "g": g, 
                    "b": b, 
                    "intensity": intensity
                }
            )
            self._rgb_color = rgb_color
            new_color_mode = ColorMode.RGB
            # 清除其他颜色模式
            self._color_temp = None
            self._hs_color = None

        # 如果没有指定任何参数，只打开灯
        if not kwargs:
            # 确保亮度不为 0
            if self._brightness == 0 or self._brightness is None:
                self._brightness = 255
                intensity = 1000
                await self._api.send_request(
                    "set_intensity", 
                    node_id=self._node_id, 
                    args={"intensity": intensity}
                )
            else:
                await self._api.send_request(
                    "set_intensity", 
                    node_id=self._node_id, 
                    args={"intensity": intensity}
                )

        # 更新颜色模式
        if new_color_mode:
            self._color_mode = new_color_mode

        self._is_on = True
        _LOGGER.debug(f"Device {self._name} turned on, new state: brightness={self._brightness}, color_mode={self._color_mode}")
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off the light."""
        _LOGGER.debug(f"Turning off device {self._name}")
        await self._api.send_request(
            "set_intensity", 
            node_id=self._node_id, 
            args={"intensity": 0}
        )
        self._is_on = False
        _LOGGER.debug(f"Device {self._name} turned off")
        self.async_write_ha_state()

    async def async_toggle(self, **kwargs) -> None:
        """Toggle the light."""
        _LOGGER.debug(f"Toggling device {self._name}, current state: {self._is_on}")
        if self._is_on:
            await self.async_turn_off(**kwargs)
        else:
            await self.async_turn_on(**kwargs)
        _LOGGER.debug(f"Device {self._name} toggled to new state: {self._is_on}")

    async def async_update(self) -> None:
        """Update the light state."""
        _LOGGER.debug(f"Updating state for device {self._name} (node_id: {self._node_id})")
        # 初始化状态变量
        new_brightness = self._brightness
        new_is_on = self._is_on
        new_color_temp = None
        new_hs_color = None
        new_rgb_color = None
        new_color_mode = None
        _LOGGER.debug(f"Current state: brightness={self._brightness}, is_on={self._is_on}, color_mode={self._color_mode}")

        # 获取亮度
        try:
            intensity_response = await self._api.send_request(
                "get_intensity", 
                node_id=self._node_id
            )
            if intensity_response and 'data' in intensity_response:
                intensity = intensity_response['data']
                # 确保intensity是数字类型
                if isinstance(intensity, (int, float)):
                    new_brightness = int(intensity * 255 / 1000)
                    new_is_on = intensity > 0
                elif isinstance(intensity, dict):
                    # 处理可能的字典格式
                    intensity_value = intensity.get('intensity', 0)
                    if isinstance(intensity_value, (int, float)):
                        new_brightness = int(intensity_value * 255 / 1000)
                        new_is_on = intensity_value > 0
                    else:
                        # 尝试获取第一个数值
                        for value in intensity.values():
                            if isinstance(value, (int, float)):
                                new_brightness = int(value * 255 / 1000)
                                new_is_on = value > 0
                                break
                        else:
                            _LOGGER.warning(f"Unexpected intensity dict format: {intensity} for device {self._name}")
                else:
                    _LOGGER.warning(f"Unexpected intensity type: {type(intensity)} for device {self._name}")
        except Exception as e:
            _LOGGER.error(f"Failed to update intensity for device {self._name}: {e}")

        # 定义颜色模式优先级: COLOR_TEMP > HS > RGB
        # 先检查当前颜色模式，优先保持
        if self._color_mode == ColorMode.COLOR_TEMP and ColorMode.COLOR_TEMP in self._color_modes:
            new_color_mode = ColorMode.COLOR_TEMP
        elif self._color_mode == ColorMode.HS and ColorMode.HS in self._color_modes:
            new_color_mode = ColorMode.HS
        elif self._color_mode == ColorMode.RGB and ColorMode.RGB in self._color_modes:
            new_color_mode = ColorMode.RGB

        # 获取色温 (最高优先级)
        if (ColorMode.COLOR_TEMP in self._color_modes and 
            (new_color_mode is None or new_color_mode == ColorMode.COLOR_TEMP)):
            try:
                cct_response = await self._api.send_request(
                    "get_cct", 
                    node_id=self._node_id
                )
                if cct_response and 'data' in cct_response:
                    cct_data = cct_response['data']
                    # 处理不同的数据格式
                    if isinstance(cct_data, (int, float)):
                        new_color_temp = cct_data
                        new_color_mode = ColorMode.COLOR_TEMP
                    elif isinstance(cct_data, dict):
                        # 尝试从字典中获取cct值
                        if 'cct' in cct_data and isinstance(cct_data['cct'], (int, float)):
                            new_color_temp = cct_data['cct']
                            new_color_mode = ColorMode.COLOR_TEMP
                        else:
                            # 尝试直接使用字典中的第一个数值
                            for key, value in cct_data.items():
                                if isinstance(value, (int, float)):
                                    new_color_temp = value
                                    new_color_mode = ColorMode.COLOR_TEMP
                                    break
                            else:
                                _LOGGER.warning(f"Unexpected CCT data format: {cct_data} for device {self._name}")
                    else:
                        _LOGGER.warning(f"Unexpected CCT data type: {type(cct_data)} for device {self._name}")
            except Exception as e:
                _LOGGER.error(f"Failed to update CCT for device {self._name}: {e}")

        # 获取HS颜色 (次高优先级)
        if (ColorMode.HS in self._color_modes and 
            (new_color_mode is None or new_color_mode == ColorMode.HS)):
            try:
                hsi_response = await self._api.send_request(
                    "get_hsi", 
                    node_id=self._node_id
                )
                if hsi_response and 'data' in hsi_response:
                    hsi_data = hsi_response['data']
                    # 处理不同的数据格式
                    if isinstance(hsi_data, dict):
                        if 'hue' in hsi_data and 'sat' in hsi_data:
                            if isinstance(hsi_data['hue'], (int, float)) and isinstance(hsi_data['sat'], (int, float)):
                                new_hs_color = (hsi_data['hue'], hsi_data['sat'])
                                new_color_mode = ColorMode.HS
                        else:
                            # 尝试从字典中提取hue和sat
                            hue = None
                            sat = None
                            for key, value in hsi_data.items():
                                if key.lower() == 'hue' and isinstance(value, (int, float)):
                                    hue = value
                                elif key.lower() == 'sat' and isinstance(value, (int, float)):
                                    sat = value
                                if hue is not None and sat is not None:
                                    break
                            if hue is not None and sat is not None:
                                new_hs_color = (hue, sat)
                                new_color_mode = ColorMode.HS
                            else:
                                _LOGGER.warning(f"Unexpected HSI data format: {hsi_data} for device {self._name}")
                    elif isinstance(hsi_data, (int, float)):
                        # 处理可能的单一数值
                        new_hs_color = (hsi_data, 100)  # 假设饱和度为100
                        new_color_mode = ColorMode.HS
                        _LOGGER.warning(f"HSI data is a single value, using default saturation: {hsi_data} for device {self._name}")
                    else:
                        _LOGGER.warning(f"Unexpected HSI data type: {type(hsi_data)} for device {self._name}")
            except Exception as e:
                _LOGGER.error(f"Failed to update HSI for device {self._name}: {e}")

        # 获取RGB颜色 (最低优先级)
        if (ColorMode.RGB in self._color_modes and 
            (new_color_mode is None or new_color_mode == ColorMode.RGB)):
            try:
                rgb_response = await self._api.send_request(
                    "get_rgb", 
                    node_id=self._node_id
                )
                if rgb_response and 'data' in rgb_response:
                    rgb_data = rgb_response['data']
                    # 处理不同的数据格式
                    if isinstance(rgb_data, dict):
                        if 'r' in rgb_data and 'g' in rgb_data and 'b' in rgb_data:
                            if isinstance(rgb_data['r'], int) and isinstance(rgb_data['g'], int) and isinstance(rgb_data['b'], int):
                                new_rgb_color = (rgb_data['r'], rgb_data['g'], rgb_data['b'])
                                new_color_mode = ColorMode.RGB
                        else:
                            # 尝试从字典中提取r, g, b
                            r = None
                            g = None
                            b = None
                            for key, value in rgb_data.items():
                                if key.lower() == 'r' and isinstance(value, int):
                                    r = value
                                elif key.lower() == 'g' and isinstance(value, int):
                                    g = value
                                elif key.lower() == 'b' and isinstance(value, int):
                                    b = value
                                if r is not None and g is not None and b is not None:
                                    break
                            if r is not None and g is not None and b is not None:
                                new_rgb_color = (r, g, b)
                                new_color_mode = ColorMode.RGB
                            else:
                                _LOGGER.warning(f"Unexpected RGB data format: {rgb_data} for device {self._name}")
                    elif isinstance(rgb_data, (int, float)):
                        # 处理可能的单一数值
                        new_rgb_color = (int(rgb_data), int(rgb_data), int(rgb_data))
                        new_color_mode = ColorMode.RGB
                        _LOGGER.warning(f"RGB data is a single value, using grayscale: {rgb_data} for device {self._name}")
                    else:
                        _LOGGER.warning(f"Unexpected RGB data type: {type(rgb_data)} for device {self._name}")
            except Exception as e:
                _LOGGER.error(f"Failed to update RGB for device {self._name}: {e}")

        # 更新状态，只保留当前颜色模式的数据
        _LOGGER.debug(f"Updating to new state: brightness={new_brightness}, is_on={new_is_on}, color_mode={new_color_mode}")
        self._brightness = new_brightness
        self._is_on = new_is_on

        if new_color_mode == ColorMode.COLOR_TEMP and new_color_temp is not None:
            self._color_temp = new_color_temp
            self._hs_color = None
            self._rgb_color = None
        elif new_color_mode == ColorMode.HS and new_hs_color is not None:
            self._color_temp = None
            self._hs_color = new_hs_color
            self._rgb_color = None
        elif new_color_mode == ColorMode.RGB and new_rgb_color is not None:
            self._color_temp = None
            self._hs_color = None
            self._rgb_color = new_rgb_color
        else:
            # 如果没有有效的颜色模式或数据，保留当前状态
            pass

        # 只有在获取到有效新颜色模式时才更新
        if new_color_mode is not None:
            self._color_mode = new_color_mode