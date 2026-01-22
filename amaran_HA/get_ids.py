#!/usr/bin/env python3
import asyncio
import json
from . import AmaranAPI

async def main():
    # 请填写您的Amaran服务器信息
    host = "localhost"
    port = 12345
    api_key = "your_api_key"

    # 创建API客户端
    api = AmaranAPI(None, host, port, api_key)
    await api.connect()

    try:
        # 获取设备列表
        print("===== 设备列表 =====")
        device_list = await api.get_device_list()
        if device_list and 'data' in device_list:
            for device in device_list['data']:
                if device['id'] != '00000000000000000000000000000000':  # 跳过群组
                    print(f"设备名称: {device['name']}")
                    print(f"  device_id: {device['id']}")
                    print(f"  node_id: {device['node_id']}")

        # 获取快照列表
        print("\n===== 快照列表 =====")
        quickshot_list = await api.get_quickshot_list()
        if quickshot_list and 'data' in quickshot_list:
            for quickshot in quickshot_list['data']:
                print(f"快照名称: {quickshot['name']}")
                print(f"  quickshot_id: {quickshot['id']}")

        # 获取预设列表
        print("\n===== 预设列表 =====")
        preset_list = await api.get_preset_list()
        if preset_list and 'data' in preset_list:
            for preset_type in preset_list['data']:
                print(f"预设类型: {preset_type['type']}")
                for preset in preset_type['list']:
                    print(f"  预设名称: {preset['name']}")
                    print(f"    preset_id: {preset['id']}")
    finally:
        await api.close()

if __name__ == "__main__":
    asyncio.run(main())