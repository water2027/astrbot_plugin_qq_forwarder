from astrbot.api import logger

from .rule import PreForwardRule
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import time

class TimeRule(PreForwardRule):
    def __init__(self, max_age: int):
        super().__init__("TimeRule")
        self.max_age = max_age

    async def evaluate(self, bot, message_id) -> bool:
        if self.max_age <= 0:
            logger.warning("[QqForwarder] TimeRule 没有配置 max_age，默认不限制消息年龄")
            return True
        msg = await bot.get_msg(message_id=message_id)
        ds = msg.get("time")
        now = int(time.time())
        if ds is None:
            logger.warning(f"[QqForwarder] TimeRule 无法获取消息 {message_id} 的时间戳，允许转发")
            return True
        
        age = now - ds
        if age > self.max_age:
            logger.info(f"[QqForwarder] TimeRule 消息 {message_id} 已经过期（{age}秒），拒绝转发")
            return False
        return True