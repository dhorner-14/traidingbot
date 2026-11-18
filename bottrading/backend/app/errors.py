"""Excepciones específicas del bot. Cada mensaje explica qué pasó y cómo resolverlo."""


class BotError(Exception):
    pass


class MexcUnavailable(BotError):
    def __init__(self, message, rate_limited=False):
        super().__init__(message)
        self.rate_limited = rate_limited


class InvalidCandleData(BotError):
    pass


class StaleDataError(BotError):
    pass


class OllamaUnavailable(BotError):
    pass


class InvalidLLMResponse(BotError):
    pass


class ModelContractError(BotError):
    pass


class ConfigError(BotError):
    pass
