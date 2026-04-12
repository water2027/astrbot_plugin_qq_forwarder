# QQ 转发插件设计文档

日期：2026-04-12

## 概述

一个 AstrBot 插件，监听源群消息，定时或按命令将未转发的消息转发到目标群。与参考插件 `astrbot_sowing_discord` 的核心区别：不依赖群活跃度，定时任务独立运行；使用游标机制避免重复转发。

## 目录结构

```
astrbot_plugin_qq_forwarder/
├── main.py                    # 插件入口：事件监听 + 调度器
├── config.py                  # 常量与路径
├── _conf_schema.json          # 配置 schema
├── core/
│   └── forward_manager.py     # 封装转发 API 调用
└── storage/
    └── cursor_store.py        # 缓存与游标持久化
```

## 配置项（_conf_schema.json）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `forward_at` | list | `["09:00","12:00","18:00"]` | 定时转发时间点，HH:MM 格式 |
| `cache_max_age` | int | `3600` | 消息缓存最大有效时长（秒） |
| `cache_size` | int | `10` | 缓存队列最大条数，超出丢弃最旧 |
| `source_group` | list | `[]` | 源群群号列表 |
| `target_group` | list | `[]` | 目标群群号列表 |
| `block_source_messages` | bool | `true` | 是否屏蔽源群消息（阻止大模型响应） |
| `allowed_message_types` | list | `["text","image","video","forward"]` | 允许转发的消息类型 |

## 存储设计

### 缓存 — `cache.json`

全局共享，所有源群的消息统一入队：

```json
[
  {"msg_id": 7890123451, "timestamp": 1744416000},
  {"msg_id": 7890123452, "timestamp": 1744416010}
]
```

- 消息入队时记录 `timestamp`（Unix 时间戳）
- `now - timestamp > cache_max_age` 的条目视为过期，转发前清理
- 队列长度超过 `cache_size` 时，丢弃最旧的条目

### 游标 — `cursor.json`

按源群记录最后一次转发到的消息ID：

```json
{
  "123456789": 7890123453
}
```

- key：源群群号（字符串）
- value：该群最后一次成功转发的 `msg_id`

## 转发逻辑

### 游标定位规则

1. 在缓存列表中查找游标值的位置
2. 若游标存在于列表中，取其之后的所有消息为待转发列表
3. 若游标不存在（为空、或对应消息已过期被清理），取缓存中全部消息
4. 待转发列表为空则跳过

### 执行流程（`_do_forward`）

1. 清理缓存中过期条目（`now - timestamp > cache_max_age`）
2. 清理超出 `cache_size` 的最旧条目
3. 对每个源群：
   a. 根据游标定位待转发消息列表
   b. 逐条调用 `forward_manager.send_forward_msg_raw(msg_id, target_id)` 转发到所有目标群
   c. 转发成功后更新该群游标为最后一条 `msg_id`
4. 所有源群处理完毕后清空缓存
5. 使用 `asyncio.Lock` 防止定时与手动并发执行

## main.py 设计

### 消息监听（`handle_message`）

- 过滤来自源群的消息
- 检查消息类型是否在 `allowed_message_types` 中
- 合法则将 `{msg_id, timestamp}` 追加到缓存
- 若 `block_source_messages` 为 true，调用 `event.stop_event()`

### 定时调度器

```python
async def initialize(self):
    self._scheduler_task = asyncio.create_task(self._scheduler_loop())

async def _scheduler_loop(self):
    while True:
        seconds = self._seconds_until_next_forward()
        await asyncio.sleep(seconds)
        async with self._forward_lock:
            await self._do_forward()

async def terminate(self):
    if self._scheduler_task and not self._scheduler_task.done():
        self._scheduler_task.cancel()
```

### 手动命令（`/来搬`）

```python
@filter.command("来搬")
async def manual_forward(self, event: AstrMessageEvent):
    if self._forward_lock.locked():
        # 回复"转发正在进行中"
        return
    async with self._forward_lock:
        await self._do_forward(event)
    # 回复"转发完成"
```

## core/forward_manager.py

复用参考插件的 `send_forward_msg_raw`，封装 `forward_group_single_msg` API 调用：

```python
async def send_forward_msg_raw(self, message_id: int, group_id: int):
    await client.api.call_action("forward_group_single_msg",
                                  group_id=group_id,
                                  message_id=message_id)
```

## storage/cursor_store.py

提供以下方法：

| 方法 | 说明 |
|------|------|
| `add_message(msg_id, timestamp)` | 追加消息到缓存 |
| `cleanup(max_age, max_size)` | 清理过期和超量条目 |
| `get_pending(group_id, cursor)` | 根据游标返回待转发消息列表 |
| `update_cursor(group_id, msg_id)` | 更新指定群的游标 |
| `get_cursor(group_id)` | 获取指定群的游标（不存在返回 None） |
| `clear_cache()` | 清空缓存队列 |

所有文件读写通过 `asyncio.Lock` 保护。
