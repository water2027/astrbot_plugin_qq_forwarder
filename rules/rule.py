from astrbot.api import logger


class PreCacheRule:
    """缓存前规则基类：此时有完整的 message 实体。"""

    def __init__(self, rule_name: str):
        self.rule_name = rule_name

    async def evaluate(self, message) -> bool:
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

    async def evaluate(self, bot, message_id: int) -> bool:
        """判断消息是否应该被转发。

        Args:
            bot: aiocqhttp bot 客户端
            message_id: 消息 ID

        Returns:
            bool: True 表示允许转发
        """


class TypeRule(PreForwardRule):
    def __init__(self, allowed_types: list[str]):
        super().__init__("TypeRule")
        self.allowed_types = allowed_types

    async def evaluate(self, bot, message_id: int) -> bool:
        if not self.allowed_types:
            return False
        ret = await bot.api.call_action("get_msg", message_id=message_id)
        messages = ret.get("message", [])
        for msg in messages:
            if msg.get("type") not in self.allowed_types:
                logger.info(f"[QqForwarder] 消息 {message_id} 包含不允许的消息类型 {msg.get('type')}")
                return False
        return True
