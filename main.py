import asyncio
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, List, Optional

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.star.filter.platform_adapter_type import PlatformAdapterType
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
from aiocqhttp.exceptions import ActionFailed


from .config import PLUGIN_NAME
from .rules.pre_cache import GroupRule, IdRule, TypeRule
from .rules.executor import PreCacheExecutor, PreForwardExecutor
from .rules.pre_forward import TimeRule
from .storage.cursor_store import CursorStore, FileStore


def _get_tool_event(context: ContextWrapper[AstrAgentContext]) -> Any:
    agent_context = getattr(context, "context", None)
    return getattr(agent_context, "event", None)


def _get_event_group_id(event: Any) -> Optional[str]:
    message_obj = getattr(event, "message_obj", None)
    group_id = getattr(message_obj, "group_id", None)
    if group_id is None:
        group_id = getattr(event, "group_id", None)
    if group_id is None:
        return None
    return str(group_id)


@dataclass
class PendingHistoryTool(FunctionTool[AstrAgentContext]):
    forwarder: Any = Field(default=None, exclude=True)
    name: str = "qq_forwarder_pending_history"
    description: str = "获取当前群还有多少条史没有搬运。"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "required": [],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        event = _get_tool_event(context)
        group_id = _get_event_group_id(event)
        if group_id is None:
            return "当前会话不是群聊，无法确定要查询哪个群的史。"

        count = await self.forwarder.get_pending_history_count(group_id)
        return f"当前群还有 {count} 条史没有搬运。"


@dataclass
class ForwardHistoryTool(FunctionTool[AstrAgentContext]):
    forwarder: Any = Field(default=None, exclude=True)
    name: str = "qq_forwarder_forward_history"
    description: str = "搬运当前群还没有搬运的史。"
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "required": [],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        event = _get_tool_event(context)
        group_id = _get_event_group_id(event)
        if group_id is None:
            return "当前会话不是群聊，无法确定要搬运到哪个群。"
        if not isinstance(event, AiocqhttpMessageEvent):
            return "当前平台不是 AIOCQHTTP，无法使用 QQ 群单条消息转发接口搬史。"

        return await self.forwarder.forward_history_for_event(event, group_id)


@register("qq_forwarder", "water2027", "QQ转发插件", "0.1.0")
class QqForwarder(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        config = config or {}

        self.forward_at: List[str] = config.get(
            "forward_at", ["09:00", "12:00", "18:00"]
        )
        self.cache_max_age: int = config.get("cache_max_age", 3600)
        self.cache_size: int = config.get("cache_size", 10)
        self.source_group: List[str] = [str(g) for g in config.get("source_group", [])]
        self.target_group: List[str] = [str(g) for g in config.get("target_group", [])]
        self.block_source_messages: bool = config.get("block_source_messages", True)
        self.allowed_msg_types: List[str] = config.get(
            "allowed_message_types", ["text", "image", "video", "forward"]
        )

        plugin_data_path = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        file_store = FileStore(plugin_data_path, self.target_group)
        self._store = CursorStore(file_store, self.cache_size)

        typeRule = TypeRule(self.allowed_msg_types)
        groupRule = GroupRule(self.source_group)
        idRule = IdRule()
        self._pre_cache_executor = PreCacheExecutor([typeRule, groupRule, idRule])

        timeRule = TimeRule(self.cache_max_age)
        self._pre_forward_executor = PreForwardExecutor([timeRule])
        self._forward_lock = asyncio.Lock()
        self._scheduler_task: Optional[asyncio.Task] = None
        self._bot_client = None  # 供定时任务使用
        self.context.add_llm_tools(
            PendingHistoryTool(forwarder=self),
            ForwardHistoryTool(forwarder=self),
        )

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
                    task = asyncio.create_task(
                        self._run_forward(targets=self.target_group)
                    )
                    task.add_done_callback(
                        lambda t: (
                            logger.error(f"[QqForwarder] 转发任务异常: {t.exception()}")
                            if not t.cancelled() and t.exception()
                            else None
                        )
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

        self.use_bot(event)

        if not await self._pre_cache_executor.evaluate(event.message_obj):
            logger.debug(f"[QqForwarder] 消息 {msg_id} 未通过缓存前规则检查，跳过缓存")
            return

        if self.block_source_messages:
            event.stop_event()

        await self._store.add_message(int(msg_id), time.time())
        logger.info(
            f"[QqForwarder] 缓存消息 {msg_id}（源群 {event.message_obj.group_id}）"
        )

    # ------------------------------------------------------------------ #
    #  手动命令
    # ------------------------------------------------------------------ #

    @filter.command("来搬")
    async def manual_forward(self, event: AstrMessageEvent):
        if self._forward_lock.locked():
            yield event.plain_result("别急, 在搬了")
            return

        self.use_bot(event)
        target_group_id = str(event.message_obj.group_id)
        task = asyncio.create_task(self._run_forward(targets=[target_group_id]))
        task.add_done_callback(
            lambda t: (
                logger.error(f"[QqForwarder] 手动转发任务异常: {t.exception()}")
                if not t.cancelled() and t.exception()
                else None
            )
        )
        yield event.plain_result("别急")

    # ------------------------------------------------------------------ #
    #  转发核心逻辑
    # ------------------------------------------------------------------ #

    async def get_pending_history_count(self, group_id: str) -> int:
        cursor = await self._store.get_cursor(group_id)
        pending = await self._store.get_pending(group_id, cursor)
        return len(pending)

    async def forward_history_for_event(
        self, event: AiocqhttpMessageEvent, group_id: str
    ) -> str:
        if self._forward_lock.locked():
            return "别急，在搬了。"

        self.use_bot(event)
        result = await self._run_forward(targets=[group_id])
        group_result = result["groups"].get(group_id)
        if result.get("error") == "no_bot":
            return "尚无可用 bot 客户端，无法搬史。"
        if not group_result:
            return "没有找到当前群的搬运结果。"

        pending = group_result["pending"]
        forwarded = group_result["forwarded"]
        skipped = group_result["skipped"]
        failed = group_result["failed"]

        if pending == 0:
            return "当前群没有需要搬运的史。"
        if failed:
            return (
                f"搬史中断：原本有 {pending} 条，已搬 {forwarded} 条，"
                f"跳过 {skipped} 条，失败 {failed} 条。"
            )
        return f"搬史完成：原本有 {pending} 条，已搬 {forwarded} 条，跳过 {skipped} 条。"

    async def _run_forward(self, targets: Optional[list[str]] = None) -> dict:
        """执行一次完整的转发流程（加锁，防止并发）。

        targets: 目标群列表。
        """
        targets = targets or []
        result = {"groups": {}, "error": None}
        async with self._forward_lock:
            if self._bot_client is None:
                logger.warning("[QqForwarder] 尚无可用 bot 客户端，跳过转发")
                result["error"] = "no_bot"
                return result

            # 记录每个目标群本次成功转发到的最后一条消息ID
            group_last_forwarded: dict = {}

            for group_id in targets:
                cursor = await self._store.get_cursor(group_id)
                pending = await self._store.get_pending(group_id, cursor)

                if not pending:
                    result["groups"][group_id] = {
                        "pending": 0,
                        "forwarded": 0,
                        "skipped": 0,
                        "failed": 0,
                    }
                    logger.info(f"[QqForwarder] 目标群 {group_id} 无待转发消息")
                    continue

                logger.info(
                    f"[QqForwarder] 目标群 {group_id} 待转发 {len(pending)} 条，游标={cursor}"
                )

                last_forwarded: Optional[int] = None
                forwarded = 0
                skipped = 0
                failed = 0
                for msg_id in pending:
                    if not await self._pre_forward_executor.evaluate(
                        self._bot_client, msg_id
                    ):
                        logger.info(f"[QqForwarder] 消息 {msg_id} 未通过规则检查，跳过")
                        skipped += 1
                        continue

                    all_success = True
                    try:
                        await self._bot_client.api.call_action(
                            "forward_group_single_msg",
                            group_id=int(group_id),
                            message_id=msg_id,
                        )
                        logger.info(
                            f"[QqForwarder] 消息 {msg_id} -> 群 {group_id} 成功"
                        )
                        forwarded += 1
                    except ActionFailed as e:
                        logger.error(
                            f"[QqForwarder] 消息 {msg_id} -> 群 {group_id} 失败: {e}"
                        )
                        all_success = False
                        failed += 1

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
                        f"[QqForwarder] 目标群 {group_id} 游标更新至 {last_forwarded}"
                    )

                result["groups"][group_id] = {
                    "pending": len(pending),
                    "forwarded": forwarded,
                    "skipped": skipped,
                    "failed": failed,
                }

            # 清理所有目标群都已转发过的消息
            # 只有全部目标群都有游标时才清理，取位置最靠前的游标作为清理边界
            if group_last_forwarded and self.target_group:
                all_cursors = []
                for group_id in self.target_group:
                    c = await self._store.get_cursor(group_id)
                    if c is not None:
                        all_cursors.append(c)

                if len(all_cursors) == len(self.target_group):
                    cache_ids = await self._store.get_all_msg_ids()
                    # 若任意群的游标不在缓存中，说明该群尚未转发过当前缓存的任何消息，不可清理
                    if all(c in cache_ids for c in all_cursors):
                        min_cursor = min(
                            all_cursors, key=lambda c: cache_ids.index(c)
                        )
                        await self._store.remove_messages_up_to(min_cursor)
                        logger.info(f"[QqForwarder] 缓存清理至游标 {min_cursor}")

        return result

    def use_bot(self, event: AstrMessageEvent):
        assert isinstance(event, AiocqhttpMessageEvent)
        if self._bot_client is None:
            self._bot_client = event.bot
