class StackCheckError(Exception):
    pass


class ConfigError(StackCheckError):
    pass


class FetchError(StackCheckError):
    pass


class IngestError(StackCheckError):
    pass


class ModelError(StackCheckError):
    pass  # provider failure after SDK retries


class SchemaError(StackCheckError):
    pass  # model output failed validation after repair


class SamParseError(StackCheckError):
    pass


class BudgetExceeded(StackCheckError):
    pass
