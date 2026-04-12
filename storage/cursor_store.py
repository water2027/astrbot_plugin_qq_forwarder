import os
import json
import time
import asyncio
from pathlib import Path
from typing import List, Optional
from astrbot.api import logger


class CursorStore:
    """管理消息缓存队列和各源群游标的持久化存储。

    cache.json 结构:
        [{"msg_id": 123, "timestamp": 1744416000}, ...]

    cursor.json 结构:
        {"群号字符串": 最后转发的msg_id整数, ...}
    """

    def __init__(self, data_dir: Path, source_groups: List[str], capacity: int = 10):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.capacity = capacity
        self._cache_file = data_dir / "cache.json"
        self._cursor_file = data_dir / "cursor.json"
        self._lock = asyncio.Lock()
        self._ensure_files(source_groups)

    def _ensure_files(self, source_groups: List[str]):
        if not self._cache_file.exists():
            with open(self._cache_file, "w", encoding="utf-8") as f:
                json.dump([], f)

        # 读取已有游标，补全 source_groups 中缺失的键（初始值为 null）
        try:
            with open(self._cursor_file, "r", encoding="utf-8") as f:
                cursors = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            cursors = {}

        updated = False
        for group_id in source_groups:
            if group_id not in cursors:
                cursors[group_id] = None
                updated = True

        if updated or not self._cursor_file.exists():
            with open(self._cursor_file, "w", encoding="utf-8") as f:
                json.dump(cursors, f)

    def _read_cache(self) -> List[dict]:
        try:
            with open(self._cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _write_cache(self, data: List[dict]):
        with open(self._cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def _read_cursors(self) -> dict:
        try:
            with open(self._cursor_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write_cursors(self, data: dict):
        with open(self._cursor_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

    async def add_message(self, msg_id: int, timestamp: float):
        """追加一条消息到缓存队列末尾。"""
        async with self._lock:
            cache = self._read_cache()
            if len(cache) >= self.capacity:
                cache.pop(0)  # 超过容量时删除最旧的条目
            cache.append({"msg_id": msg_id, "timestamp": timestamp})
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
            return msg_ids[idx + 1 :]

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

    async def remove_messages_up_to(self, msg_id: int):
        """从缓存队列头部删除直到（含）msg_id 的所有条目。

        用于只清理已成功转发的消息，保留失败及之后的消息。
        """
        async with self._lock:
            cache = self._read_cache()
            msg_ids = [e["msg_id"] for e in cache]
            if msg_id not in msg_ids:
                return
            idx = msg_ids.index(msg_id)
            self._write_cache(cache[idx + 1 :])

    async def get_all_msg_ids(self) -> List[int]:
        """返回缓存队列中所有消息ID的有序列表。"""
        async with self._lock:
            cache = self._read_cache()
            return [e["msg_id"] for e in cache]

    async def clear_cache(self):
        """清空缓存队列（所有源群处理完毕后调用）。"""
        async with self._lock:
            self._write_cache([])
