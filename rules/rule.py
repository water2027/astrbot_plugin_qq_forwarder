from astrbot.api import logger


class Rule:
    """评价一条转发消息是否应该被转发的抽象基类, 任何具体的规则需要继承此类

    Attributes:
        rule_name (str): 规则名称
    """

    def __init__(self, rule_name: str):
        self.rule_name = rule_name

    async def evaluate(self, bot, message_id: int) -> bool:
        """评价一条转发消息是否应该被转发

        Args:
            bot: aiocqhttp bot 客户端
            message_id (int): 消息id

        Returns:
            bool: 是否应该被转发
        """


class TypeRule(Rule):
    def __init__(self, allowed_types: list[str]):
        super().__init__("TypeRule")
        self.allowed_types = allowed_types

    async def evaluate(self, bot, message_id: int) -> bool:
        if not self.allowed_types:
            return False
        ret = await bot.api.call_action("get_msg", message_id=message_id)
        logger.info(f"TypeRule: {ret}")
        return True