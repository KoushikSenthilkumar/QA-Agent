"""
VisionProvider factory.

Selects the correct provider based on config/environment.
"""

from __future__ import annotations

import os

from qa_agent.utils.config import QAConfig
from qa_agent.utils.logger import get_logger
from qa_agent.vision.base import VisionProvider

logger = get_logger(__name__)


def create_vision_provider(config: QAConfig) -> VisionProvider:
    """
    Create the appropriate VisionProvider based on configuration.

    Provider selection priority:
    1. VLM_PROVIDER environment variable
    2. config.vision.provider
    3. Default to mock

    This ensures CI/CD never accidentally makes real API calls.
    """
    provider_name = (
        os.getenv("VLM_PROVIDER") or config.vision.provider or "mock"
    ).lower()

    if provider_name == "openai":
        api_key = os.getenv("VLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning(
                "VLM_PROVIDER=openai but no API key found. Falling back to mock provider. "
                "Set VLM_API_KEY in your .env file."
            )
            provider_name = "mock"
        else:
            try:
                from qa_agent.vision.openai_provider import OpenAIProvider
                return OpenAIProvider(
                    model=os.getenv("VLM_MODEL") or config.vision.model,
                    max_tokens=config.vision.max_tokens,
                    temperature=config.vision.temperature,
                    timeout=config.vision.timeout,
                )
            except Exception as e:
                logger.error(f"Failed to create OpenAI provider: {e}. Falling back to mock.")
                provider_name = "mock"

    if provider_name == "mock":
        from qa_agent.vision.mock_provider import MockVisionProvider
        return MockVisionProvider()

    logger.warning(f"Unknown VLM provider '{provider_name}'. Using mock.")
    from qa_agent.vision.mock_provider import MockVisionProvider
    return MockVisionProvider()
