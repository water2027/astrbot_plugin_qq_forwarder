import re

from astrbot.api import logger

from .rule import PreCacheRule
from aiocqhttp import Event


class TypeRule(PreCacheRule):
    def __init__(self, allowed_types: list[str]):
        super().__init__("TypeRule")
        self.allowed_types = allowed_types

    async def evaluate(self, message) -> bool:
        if not self.allowed_types:
            logger.warning("[QqForwarder] TypeRule 没有配置 allowed_message_types，默认拒绝所有消息")
            return False
        
        found_types = set()
        if isinstance(message.raw_message, Event):
            msg = message.raw_message.raw_message
            if "[CQ:image" in msg: found_types.add("image")
            if "[CQ:video" in msg: found_types.add("video")
            if "[CQ:forward" in msg or "[CQ:node" in msg: found_types.add("forward")

            text_only = re.sub(r'\[CQ:.*?\]', '', msg).strip()
            if text_only:
                found_types.add("text")

        else:
            return False

        for t in ["image", "video", "forward"]:
            if t in found_types and t not in self.allowed_types:
                return False
            
        if not found_types.intersection(self.allowed_types):
            return False

        return True
    
class GroupRule(PreCacheRule):
    def __init__(self, allowed_source: list[int]):
        super().__init__("GroupRule")
        self.allowed_source = allowed_source

    async def evaluate(self, message) -> bool:
        if not self.allowed_source:
            logger.warning("[QqForwarder] GroupRule 没有配置 allowed_source，默认拒绝所有消息")
            return False
        
        group_id = message.group_id
        if group_id in self.allowed_source:
            return True
        else:
            logger.info(f"[QqForwarder] GroupRule 消息 {message.message_id} 来自不允许的群 {group_id}，拒绝")
            return False
    
class IdRule(PreCacheRule):
    def __init__(self):
        super().__init__("IdRule")

    async def evaluate(self, message) -> bool:
        msg_id = message.message_id
        try:
            int(msg_id)
            return True
        except (ValueError, TypeError):
            logger.warning(f"[QqForwarder] IdRule 消息 ID {msg_id} 非数字，拒绝")
            return False