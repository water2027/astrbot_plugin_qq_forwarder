from astrbot.api.platform import AstrBotMessage
from aiocqhttp import CQHttp

class PreCacheRule:
    """缓存前规则基类：此时有完整的 message 实体。"""

    def __init__(self, rule_name: str):
        self.rule_name = rule_name

    async def evaluate(self, message: AstrBotMessage) -> bool:
        """判断消息是否应该入缓存。

        Args:
            message: 消息内容（list/str，AstrBot 框架传入的原始格式）

        Returns:
            bool: True 表示允许入缓存
        """


class PreForwardRule:
    """转发前规则基类：此时只有消息 ID，需要通过 bot 查询详情。"""

    def __init__(self, rule_name: str):
        self.rule_name = rule_name

    async def evaluate(self, bot: CQHttp, message_id: int) -> bool:
        """判断消息是否应该被转发。

        Args:
            bot: aiocqhttp bot 客户端
            message_id: 消息 ID

        Returns:
            bool: True 表示允许转发
        """

