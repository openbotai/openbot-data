"""Public exceptions for invalid API arguments and unavailable datasets."""


class OpenBotDataError(Exception):
    """Base exception for OpenBot Data API failures."""


class DatasetNotFoundError(OpenBotDataError):
    """Raised when a requested dataset root does not exist."""


class DatasetArgumentError(OpenBotDataError, ValueError):
    """Raised when a caller supplies an unsupported inspection option."""
