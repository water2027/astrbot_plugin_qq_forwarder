# QQ 转发插件实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现一个 AstrBot 插件，监听源群消息缓存，定时或按命令将未转发的消息通过游标机制转发到目标群，避免重复转发。

**Architecture:** 插件分为四个模块：`config.py` 提供路径常量，`storage/cursor_store.py` 负责缓存与游标的 JSON 持久化，`core/forward_manager.py` 封装 OneBot API 调用，`main.py` 负责事件监听、定时调度和命令处理。转发时以消息ID游标定位待转发消息，所有源群共享一个缓存队列。

**Tech Stack:** Python 3.10+, AstrBot 插件 API, aiocqhttp (OneBot v11), asyncio, JSON 文件存储

---

## 文件清单

| 文件 | 操作 | 职责 |
|------|------|------|
| `config.py` | 创建 | 存储目录常量 |
| `_conf_schema.json` | 修改 | 插件配置 schema |
| `storage/__init__.py` | 创建 | 空包标记 |
| `storage/cursor_store.py` | 创建 | 缓存与游标持久化 |
| `core/__init__.py` | 创建 | 空包标记 |
| `core/forward_manager.py` | 创建 | 封装转发 API |
| `main.py` | 修改 | 插件入口 |

---

## Task 1: config.py — 路径常量

**Files:**
- Create: `config.py`

- [ ] **Step 1: 创建 config.py**

```python
import os

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(CURRENT_DIR))
DATA_DIR = os.path.join(ROOT_DIR, "qq_forwarder_data")

os.makedirs(DATA_DIR, exist_ok=True)
```

- [ ] **Step 2: Commit**

```bash
git add config.py
git commit -m "feat: add config.py with data directory constants"
```

---

## Task 2: _conf_schema.json — 配置 schema

**Files:**
- Modify: `_conf_schema.json`

- [ ] **Step 1: 替换 _conf_schema.json 内容**

```json
{
  "forward_at": {
    "type": "list",
    "default": ["09:00", "12:00", "18:00"],
    "description": "转发时间点列表, 格式: HH:MM, 24小时制"
  },
  "cache_max_age": {
    "type": "int",
    "default": 3600,
    "description": "消息缓存最大有效时长（秒），超出此时长的消息在转发前会被清理"
  },
  "cache_size": {
    "type": "int",
    "default": 10,
    "description": "缓存队列最大条数，超出时丢弃最旧的消息，设为 0 表示不限制"
  },
  "source_group": {
    "type": "list",
    "default": [],
    "description": "来源群列表"
  },
  "target_group": {
    "type": "list",
    "default": [],
    "description": "转发目标群列表"
  },
  "block_source_messages": {
    "type": "bool",
    "default": true,
    "description": "是否屏蔽源群消息",
    "hint": "开启后将屏蔽 source_group 中的群消息，阻止大模型和下游插件响应"
  },
  "allowed_message_types": {
    "type": "list",
    "default": ["text", "image", "video", "forward"],
    "description": "允许转发的消息类型",
    "hint": "可选: text(文本), image(图片), video(视频), forward(聊天记录/合并转发)。消息包含未被允许的媒体类型时整条消息不转发。",
    "options": ["text", "image", "video", "forward"]
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add _conf_schema.json
git commit -m "feat: update _conf_schema.json for qq forwarder"
```

---

## Task 3: storage/cursor_store.py — 缓存与游标持久化

**Files:**
- Create: `storage/__init__.py`
- Create: `storage/cursor_store.py`

- [ ] **Step 1: 创建 storage/__init__.py**

```python
```
（空文件）

- [ ] **Step 2: 创建 storage/cursor_store.py**

```python
import os
import json
import time
import asyncio
from typing import List, Optional
from astrbot.api import logger
from ..config import DATA_DIR

CACHE_FILE = os.path.join(DATA_DIR, "cache.json")
CURSOR_FILE = os.path.join(DATA_DIR, "cursor.json")


class CursorStore:
    """管理消息缓存队列和各源群游标的持久化存储。

    cache.json 结构:
        [{"msg_id": 123, "timestamp": 1744416000}, ...]

    cursor.json 结构:
        {"群号字符串": 最后转发的msg_id整数, ...}
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._ensure_files()

    def _ensure_files(self):
        for path, default in [(CACHE_FILE, []), (CURSOR_FILE, {})]:
            if not os.path.exists(path):
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(default, f)

    def _read_cache(self) -> List[dict]:
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _write_cache(self, data: List[dict]):
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def _read_cursors(self) -> dict:
        try:
            with open(CURSOR_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write_cursors(self, data: dict):
        with open(CURSOR_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)

    async def add_message(self, msg_id: int, timestamp: float):
        """追加一条消息到缓存队列末尾。"""
        async with self._lock:
            cache = self._read_cache()
            cache.append({"msg_id": msg_id, "timestamp": timestamp})
            self._write_cache(cache)

    async def cleanup(self, max_age: int, max_size: int):
        """清理过期和超量的缓存条目。

        先按 max_age 删除过期条目，再按 max_size 从最旧端截断（max_size=0 不限制）。
        """
        async with self._lock:
            cache = self._read_cache()
            now = time.time()

            # 清理过期
            cache = [e for e in cache if now - e["timestamp"] <= max_age]

            # 清理超量（保留最新的 max_size 条）
            if max_size > 0 and len(cache) > max_size:
                cache = cache[-max_size:]

            self._write_cache(cache)

    async def get_pending(self, group_id: str, cursor: Optional[int]) -> List[int]:
        """根据游标返回待转发的 msg_id 列表。

        若 cursor 不在缓存中（为 None 或已过期被清理），返回全部缓存中的 msg_id。
        若 cursor 存在，返回其之后的所有 msg_id。
        """
        async with self._lock:
            cache = self._read_cache()

        if not cache:
            return []

        msg_ids = [e["msg_id"] for e in cache]

        if cursor is None or cursor not in msg_ids:
            return msg_ids

        idx = msg_ids.index(cursor)
        return msg_ids[idx + 1:]

    async def update_cursor(self, group_id: str, msg_id: int):
        """更新指定源群的游标为 msg_id。"""
        async with self._lock:
            cursors = self._read_cursors()
            cursors[group_id] = msg_id
            self._write_cursors(cursors)

    async def get_cursor(self, group_id: str) -> Optional[int]:
        """获取指定源群的游标，不存在返回 None。"""
        async with self._lock:
            cursors = self._read_cursors()
            return cursors.get(group_id)

    async def clear_cache(self):
        """清空缓存队列（所有源群处理完毕后调用）。"""
        async with self._lock:
            self._write_cache([])
```

- [ ] **Step 3: Commit**

```bash
git add storage/__init__.py storage/cursor_store.py
git commit -m "feat: add CursorStore for cache and cursor persistence"
```

---

## Task 4: core/forward_manager.py — 封装转发 API

**Files:**
- Create: `core/__init__.py`
- Create: `core/forward_manager.py`

- [ ] **Step 1: 创建 core/__init__.py**

```python
```
（空文件）

- [ ] **Step 2: 创建 core/forward_manager.py**

```python
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.api import logger


class ForwardManager:
    """封装 OneBot API 的转发调用。"""

    def __init__(self, event: AiocqhttpMessageEvent):
        self.client = event.bot

    async def send_forward_msg_raw(self, message_id: int, group_id: int):
        """将指定消息原样转发到目标群。

        Args:
            message_id: 要转发的消息 ID
            group_id: 目标群号
        """
        await self.client.api.call_action(
            "forward_group_single_msg",
            group_id=group_id,
            message_id=message_id,
        )
        logger.info(
            f"[QqForwarder] 转发消息 {message_id} -> 群 {group_id} 成功"
        )
```

- [ ] **Step 3: Commit**

```bash
git add core/__init__.py core/forward_manager.py
git commit -m "feat: add ForwardManager wrapping OneBot forward API"
```

---

## Task 5: main.py — 消息类型过滤工具函数

**Files:**
- Modify: `main.py`

该步骤只添加消息类型判断的辅助函数，不涉及事件监听和调度器，便于单独验证。

- [ ] **Step 1: 将 main.py 替换为以下内容（含类型过滤函数，其余方法留空占位）**

```python
import asyncio
import re
import time
from datetime import datetime, time as dtime
from typing import List, Optional

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.core.star.filter.platform_adapter_type import PlatformAdapterType
from aiocqhttp.exceptions import ActionFailed

from .core.forward_manager import ForwardManager
from .storage.cursor_store import CursorStore


@register("qq_forwarder", "water2027", "QQ转发插件", "0.1.0")
class QqForwarder(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        config = config or {}

        self.forward_at: List[str] = config.get("forward_at", ["09:00", "12:00", "18:00"])
        self.cache_max_age: int = config.get("cache_max_age", 3600)
        self.cache_size: int = config.get("cache_size", 10)
        self.source_group: List[str] = [str(g) for g in config.get("source_group", [])]
        self.target_group: List[int] = [int(g) for g in config.get("target_group", [])]
        self.block_source_messages: bool = config.get("block_source_messages", True)
        self.allowed_msg_types: List[str] = config.get(
            "allowed_message_types", ["text", "image", "video", "forward"]
        )

        self._store = CursorStore()
        self._forward_lock = asyncio.Lock()
        self._scheduler_task: Optional[asyncio.Task] = None
        self._bot_client = None  # 首次收到消息时记录，供定时任务使用

    # ------------------------------------------------------------------ #
    #  消息类型过滤（复用参考插件逻辑，支持 dict / Component / str 三种格式）
    # ------------------------------------------------------------------ #

    def _is_allowed_msg_type(self, message) -> bool:
        """判断消息是否包含允许的消息类型。

        规则：
        1. 若消息含有未授权的核心媒体类型（image/video/forward），拒绝整条消息。
        2. 若消息没有任何符合授权的元素，拒绝。
        """
        if not self.allowed_msg_types:
            return False

        allowed = set(self.allowed_msg_types)
        found_types: set = set()

        if isinstance(message, list):
            for seg in message:
                if isinstance(seg, dict):
                    mtype = seg.get("type", "")
                    if mtype == "image":
                        found_types.add("image")
                    elif mtype == "video":
                        found_types.add("video")
                    elif mtype in ["forward", "node"]:
                        found_types.add("forward")
                    elif mtype in ["text", "face", "at", "reply"]:
                        if mtype == "text" and not seg.get("data", {}).get("text", "").strip():
                            continue
                        found_types.add("text")
                else:
                    cname = seg.__class__.__name__.lower()
                    if cname == "image":
                        found_types.add("image")
                    elif cname == "video":
                        found_types.add("video")
                    elif cname in ["forward", "node"]:
                        found_types.add("forward")
                    elif cname in ["plain", "text", "face", "at", "reply"]:
                        if cname in ["plain", "text"] and not getattr(seg, "text", "").strip():
                            continue
                        found_types.add("text")
        elif isinstance(message, str):
            if "[CQ:image" in message:
                found_types.add("image")
            if "[CQ:video" in message:
                found_types.add("video")
            if "[CQ:forward" in message or "[CQ:node" in message:
                found_types.add("forward")
            text_only = re.sub(r"\[CQ:.*?\]", "", message).strip()
            if text_only:
                found_types.add("text")

        for t in ["image", "video", "forward"]:
            if t in found_types and t not in allowed:
                return False

        if not found_types.intersection(allowed):
            return False

        return True

    # ------------------------------------------------------------------ #
    #  调度器辅助
    # ------------------------------------------------------------------ #

    def _seconds_until_next_forward(self) -> float:
        """计算距下一个转发时间点的秒数（最少 1 秒）。"""
        now = datetime.now()
        candidates = []
        for ts in self.forward_at:
            try:
                parts = ts.split(":")
                h, m = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
                target = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if target <= now:
                    # 已过，推到明天
                    target = target.replace(day=target.day + 1)
                candidates.append((target - now).total_seconds())
            except Exception:
                logger.warning(f"[QqForwarder] 无法解析转发时间点: {ts}")
        if not candidates:
            return 3600.0
        return max(1.0, min(candidates))

    async def initialize(self):
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("[QqForwarder] 定时调度器已启动")

    async def terminate(self):
        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()
            logger.info("[QqForwarder] 定时调度器已停止")

    async def _scheduler_loop(self):
        while True:
            seconds = self._seconds_until_next_forward()
            logger.info(f"[QqForwarder] 距下次定时转发 {seconds:.0f} 秒")
            await asyncio.sleep(seconds)
            if not self._forward_lock.locked():
                asyncio.create_task(self._run_forward(client=None))

    # ------------------------------------------------------------------ #
    #  事件监听
    # ------------------------------------------------------------------ #

    @filter.platform_adapter_type(PlatformAdapterType.AIOCQHTTP)
    async def handle_message(self, event: AstrMessageEvent):
        source_group_id = str(event.message_obj.group_id)
        if source_group_id not in self.source_group:
            return

        if self.block_source_messages:
            event.stop_event()

        msg_id = event.message_obj.message_id
        raw_msg = getattr(event.message_obj, "message", getattr(event.message_obj, "raw_message", ""))

        if not self._is_allowed_msg_type(raw_msg):
            logger.debug(f"[QqForwarder] 消息 {msg_id} 类型不允许，跳过缓存")
            return

        try:
            int(msg_id)
        except (ValueError, TypeError):
            logger.warning(f"[QqForwarder] 消息 ID {msg_id} 非数字，跳过")
            return

        await self._store.add_message(int(msg_id), time.time())
        logger.info(f"[QqForwarder] 缓存消息 {msg_id}（源群 {source_group_id}）")

    # ------------------------------------------------------------------ #
    #  手动命令
    # ------------------------------------------------------------------ #

    @filter.command("来搬")
    async def manual_forward(self, event: AstrMessageEvent):
        if self._forward_lock.locked():
            yield event.plain_result("转发正在进行中，请稍候。")
            return

        asyncio.create_task(self._run_forward(client=event))
        yield event.plain_result("开始转发，完成后不会另行通知。")

    # ------------------------------------------------------------------ #
    #  转发核心逻辑
    # ------------------------------------------------------------------ #

    async def _run_forward(self, client=None):
        """执行一次完整的转发流程（加锁，防止并发）。"""
        async with self._forward_lock:
            await self._store.cleanup(self.cache_max_age, self.cache_size)

            forwarded_any = False
            for group_id in self.source_group:
                cursor = await self._store.get_cursor(group_id)
                pending = await self._store.get_pending(group_id, cursor)

                if not pending:
                    logger.info(f"[QqForwarder] 源群 {group_id} 无待转发消息")
                    continue

                logger.info(
                    f"[QqForwarder] 源群 {group_id} 待转发 {len(pending)} 条，游标={cursor}"
                )

                last_forwarded: Optional[int] = None
                for msg_id in pending:
                    for target_id in self.target_group:
                        try:
                            # ForwardManager 需要一个带 bot 属性的 event 或 client
                            # 定时任务无 event，改用直接保存 bot 引用的方式（见 handle_message）
                            # 这里用 _bot_client（在第一次 handle_message 时记录）
                            if self._bot_client is None:
                                logger.warning("[QqForwarder] 尚无可用 bot 客户端，跳过转发")
                                return
                            await self._bot_client.api.call_action(
                                "forward_group_single_msg",
                                group_id=target_id,
                                message_id=msg_id,
                            )
                            logger.info(
                                f"[QqForwarder] 消息 {msg_id} -> 群 {target_id} 成功"
                            )
                        except ActionFailed as e:
                            logger.error(
                                f"[QqForwarder] 消息 {msg_id} -> 群 {target_id} 失败: {e}"
                            )
                    last_forwarded = msg_id

                if last_forwarded is not None:
                    await self._store.update_cursor(group_id, last_forwarded)
                    forwarded_any = True

            if forwarded_any:
                await self._store.clear_cache()
                logger.info("[QqForwarder] 本次转发完成，缓存已清空")
```

注意：上面的代码引用了 `self._bot_client`，这个属性将在 Task 6 中添加。

- [ ] **Step 2: Commit**

```bash
git add main.py
git commit -m "feat: add message type filter and scheduler skeleton in main.py"
```

---

## Task 6: main.py — 在 handle_message 里记录 bot client

**Files:**
- Modify: `main.py`

定时任务没有 `event` 对象，需要在第一次收到源群消息时把 `event.bot` 保存到 `self._bot_client`。

- [ ] **Step 1: 在 handle_message 里记录 bot client**

在 `handle_message` 方法的 `source_group_id not in self.source_group` 判断之后、`block_source_messages` 判断之前，添加一行：

```python
        if self._bot_client is None:
            self._bot_client = event.bot
```

- [ ] **Step 2: Commit**

```bash
git add main.py
git commit -m "feat: capture bot client on first source group message for scheduler use"
```

---

## Task 7: main.py — 修正 _seconds_until_next_forward 跨日计算

**Files:**
- Modify: `main.py`

当前 `_seconds_until_next_forward` 用 `target.replace(day=target.day + 1)` 跨月时会出错，改用 `timedelta`。

- [ ] **Step 1: 在文件顶部导入 timedelta**

在 `from datetime import datetime, time as dtime` 这行改为：

```python
from datetime import datetime, time as dtime, timedelta
```

- [ ] **Step 2: 修改 _seconds_until_next_forward 方法**

将整个 `_seconds_until_next_forward` 方法替换为：

```python
    def _seconds_until_next_forward(self) -> float:
        """计算距下一个转发时间点的秒数（最少 1 秒）。"""
        now = datetime.now()
        candidates = []
        for ts in self.forward_at:
            try:
                parts = ts.split(":")
                h, m = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
                target = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                candidates.append((target - now).total_seconds())
            except Exception:
                logger.warning(f"[QqForwarder] 无法解析转发时间点: {ts}")
        if not candidates:
            return 3600.0
        return max(1.0, min(candidates))
```

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "fix: use timedelta for cross-month day calculation in scheduler"
```

---

## Task 8: 整体验证与收尾

**Files:**
- Modify: `metadata.yaml`

- [ ] **Step 1: 更新 metadata.yaml 版本号**

将 `version: v0.0.1` 改为 `version: v0.1.0`。

- [ ] **Step 2: 确认目录结构**

运行：

```bash
find /home/water/work/astrbot_plugin_qq_forwarder -name "*.py" | grep -v astrbot_sowing_discord | grep -v __pycache__ | sort
```

期望输出包含：

```
.../config.py
.../core/__init__.py
.../core/forward_manager.py
.../main.py
.../storage/__init__.py
.../storage/cursor_store.py
```

- [ ] **Step 3: 检查导入是否正确（语法检查）**

```bash
cd /home/water/work/astrbot_plugin_qq_forwarder && python3 -c "
import ast, sys
files = ['config.py', 'storage/cursor_store.py', 'core/forward_manager.py', 'main.py']
for f in files:
    try:
        ast.parse(open(f).read())
        print(f'OK: {f}')
    except SyntaxError as e:
        print(f'FAIL: {f} - {e}')
        sys.exit(1)
"
```

期望输出：

```
OK: config.py
OK: storage/cursor_store.py
OK: core/forward_manager.py
OK: main.py
```

- [ ] **Step 4: Commit**

```bash
git add metadata.yaml
git commit -m "chore: bump version to v0.1.0"
```
