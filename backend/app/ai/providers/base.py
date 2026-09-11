"""Base interface for AI providers."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class BaseAIProvider(ABC):
    """Abstract base class defining unified capabilities for AI providers."""

    @abstractmethod
    async def generate_text(
        self,
        prompt: str,
        model: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
    ) -> str:
        """Generate text completion from a prompt."""
        raise NotImplementedError

    @abstractmethod
    async def stream_text(
        self,
        prompt: str,
        model: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Stream text tokens completion from a prompt."""
        raise NotImplementedError
        yield ""  # pragma: no cover

    @abstractmethod
    async def generate_structured(
        self,
        prompt: str,
        model: str,
        response_model: type[T],
        system_prompt: str | None = None,
        temperature: float = 0.7,
    ) -> T:
        """Generate structured data conforming to a Pydantic model."""
        raise NotImplementedError
