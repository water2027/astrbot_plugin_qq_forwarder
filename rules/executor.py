from .rule import Rule


class RuleExecutor:
    def __init__(self, rules: list[Rule]):
        self.rules = rules

    def add_rule(self, rule: Rule):
        self.rules.append(rule)

    async def evaluate(self, bot, message_id: int) -> bool:
        for rule in self.rules:
            if not await rule.evaluate(bot, message_id):
                return False
        return True