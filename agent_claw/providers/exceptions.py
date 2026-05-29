"""Provider-side errors. The orchestrator treats Transient as retryable
(the LLM may re-call the tool) and Permanent as terminal (surface to user)."""


class ProviderError(Exception):
    """Base class for any external-provider failure."""


class TransientProviderError(ProviderError):
    """Rate limit, 5xx, connection blip. Retry may succeed."""


class PermanentProviderError(ProviderError):
    """4xx (other than 429), bad config, no data. Retrying will not help."""
