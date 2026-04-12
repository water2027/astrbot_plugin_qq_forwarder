from .rule import PreCacheRule, PreForwardRule
from astrbot.api.platform import AstrBotMessage
from aiocqhttp import CQHttp


class PreCacheExecutor:
    """缓存前规则执行器，evaluate 接收 message 实体。"""

    def __init__(self, rules: list[PreCacheRule]):
        self.rules = rules

    def add_rule(self, rule: PreCacheRule):
        self.rules.append(rule)

    async def evaluate(self, message: AstrBotMessage) -> bool:
        for rule in self.rules:
            if not await rule.evaluate(message):
                return False
        return True


class PreForwardExecutor:
    """转发前规则执行器，evaluate 接收 bot 客户端和消息 ID。"""

    def __init__(self, rules: list[PreForwardRule]):
        self.rules = rules

    def add_rule(self, rule: PreForwardRule):
        self.rules.append(rule)

    async def evaluate(self, bot: CQHttp, message_id: int) -> bool:
        for rule in self.rules:
            if not await rule.evaluate(bot, message_id):
                return False
        return True
