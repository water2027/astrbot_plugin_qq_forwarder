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

    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self._cache_file = data_dir / "cache.json"
        self._cursor_file = data_dir / "cursor.json"
        self._lock = asyncio.Lock()
        self._ensure_files()

    def _ensure_files(self):
        for path, default in [(self._cache_file, []), (self._cursor_file, {})]:
            if not path.exists():
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(default, f)

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
            if max_age > 0:
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
            self._write_cache(cache[idx + 1:])

    async def get_all_msg_ids(self) -> List[int]:
        """返回缓存队列中所有消息ID的有序列表。"""
        async with self._lock:
            cache = self._read_cache()
            return [e["msg_id"] for e in cache]

    async def clear_cache(self):
        """清空缓存队列（所有源群处理完毕后调用）。"""
        async with self._lock:
            self._write_cache([])
