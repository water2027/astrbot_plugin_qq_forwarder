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
