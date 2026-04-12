import asyncio
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional


from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.platform_adapter_type import PlatformAdapterType
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from aiocqhttp.exceptions import ActionFailed


from .config import PLUGIN_NAME
from .rules.pre_cache import GroupRule, IdRule, TypeRule
from .rules.executor import PreCacheExecutor, PreForwardExecutor
from .rules.pre_forward import TimeRule
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

        plugin_data_path = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        self._store = CursorStore(plugin_data_path)

        typeRule = TypeRule(self.allowed_msg_types)
        groupRule = GroupRule(self.source_group)
        idRule = IdRule()
        self._pre_cache_executor = PreCacheExecutor([typeRule, groupRule, idRule])

        timeRule = TimeRule(self.cache_max_age)
        self._pre_forward_executor = PreForwardExecutor([timeRule])
        self._forward_lock = asyncio.Lock()
        self._scheduler_task: Optional[asyncio.Task] = None
        self._bot_client = None  # 首次收到消息时记录，供定时任务使用

    # ------------------------------------------------------------------ #
    #  调度器
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
                    target += timedelta(days=1)
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
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            logger.info("[QqForwarder] 定时调度器已停止")

    async def _scheduler_loop(self):
        try:
            while True:
                seconds = self._seconds_until_next_forward()
                logger.info(f"[QqForwarder] 距下次定时转发 {seconds:.0f} 秒")
                await asyncio.sleep(seconds)
                if not self._forward_lock.locked():
                    task = asyncio.create_task(self._run_forward())
                    task.add_done_callback(
                        lambda t: logger.error(f"[QqForwarder] 转发任务异常: {t.exception()}") if not t.cancelled() and t.exception() else None
                    )
        except asyncio.CancelledError:
            raise

    # ------------------------------------------------------------------ #
    #  事件监听
    # ------------------------------------------------------------------ #

    @filter.platform_adapter_type(PlatformAdapterType.AIOCQHTTP)
    async def handle_message(self, event: AstrMessageEvent):
        assert isinstance(event, AiocqhttpMessageEvent)

        msg_id = event.message_obj.message_id

        if not await self._pre_cache_executor.evaluate(event.message_obj):
            logger.debug(f"[QqForwarder] 消息 {msg_id} 未通过缓存前规则检查，跳过缓存")
            return

        if self._bot_client is None:
            self._bot_client = event.bot

        if self.block_source_messages:
            event.stop_event()

        await self._store.add_message(int(msg_id), time.time())
        logger.info(f"[QqForwarder] 缓存消息 {msg_id}（源群 {event.message_obj.group_id}）")

    # ------------------------------------------------------------------ #
    #  手动命令
    # ------------------------------------------------------------------ #

    @filter.command("来搬")
    async def manual_forward(self, event: AstrMessageEvent):
        if self._forward_lock.locked():
            yield event.plain_result("别急, 在般了")
            return

        task = asyncio.create_task(self._run_forward())
        task.add_done_callback(
            lambda t: logger.error(f"[QqForwarder] 手动转发任务异常: {t.exception()}") if not t.cancelled() and t.exception() else None
        )
        yield event.plain_result("别急")

    # ------------------------------------------------------------------ #
    #  转发核心逻辑
    # ------------------------------------------------------------------ #

    async def _run_forward(self):
        """执行一次完整的转发流程（加锁，防止并发）。

        每个源群有独立游标，定时触发时各群独立计算待转发消息。
        缓存是统一队列，转发完成后只清理所有群都已覆盖的部分。
        """
        async with self._forward_lock:
            if self._bot_client is None:
                logger.warning("[QqForwarder] 尚无可用 bot 客户端，跳过转发")
                return

            # 记录每个源群本次成功转发到的最后一条消息ID
            group_last_forwarded: dict = {}

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
                    if not await self._pre_forward_executor.evaluate(self._bot_client, msg_id):
                        logger.info(f"[QqForwarder] 消息 {msg_id} 未通过规则检查，跳过")
                        continue

                    all_success = True
                    for target_id in self.target_group:
                        try:
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
                            all_success = False

                    if not all_success:
                        logger.warning(
                            f"[QqForwarder] 消息 {msg_id} 转发不完整，停止群 {group_id} 本次转发"
                        )
                        break

                    last_forwarded = msg_id

                if last_forwarded is not None:
                    await self._store.update_cursor(group_id, last_forwarded)
                    group_last_forwarded[group_id] = last_forwarded
                    logger.info(
                        f"[QqForwarder] 源群 {group_id} 游标更新至 {last_forwarded}"
                    )

            # 清理所有群都已转发过的消息（取所有群游标中位置最靠前的）
            if group_last_forwarded:
                # 读取所有群的最新游标（含本次未更新的群）
                all_cursors = []
                for group_id in self.source_group:
                    c = await self._store.get_cursor(group_id)
                    if c is not None:
                        all_cursors.append(c)

                if len(all_cursors) == len(self.source_group):
                    # 所有群都有游标，找位置最靠前（值最小，在缓存中index最小）的游标
                    # 该游标之前的消息所有群都已转发过，可以安全删除
                    cache_ids = await self._store.get_all_msg_ids()
                    valid_cursors = [c for c in all_cursors if c in cache_ids]
                    if len(valid_cursors) == len(self.source_group):
                        min_cursor = min(valid_cursors, key=lambda c: cache_ids.index(c))
                        await self._store.remove_messages_up_to(min_cursor)
                        logger.info(f"[QqForwarder] 缓存清理至游标 {min_cursor}")
