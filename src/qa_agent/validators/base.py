"""
BaseValidator — abstract base for all QA validators.

Every validator:
1. Receives a PageState and/or a PageController
2. Produces a list of Finding objects
3. Never crashes the overall QA run (errors are caught and logged)
4. Is completely independent from other validators
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from qa_agent.utils.logger import get_logger
from qa_agent.utils.schemas import Finding, PageState


class BaseValidator(ABC):
    """
    Abstract base class for all QA validators.

    Validators are stateless — each call to validate() is independent.
    They should not store cross-run state.
    """

    def __init__(self):
        self.logger = get_logger(self.__class__.__module__)

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @abstractmethod
    async def validate(self, page_state: PageState, **kwargs) -> list[Finding]:
        """
        Run validation against a captured page state.

        Args:
            page_state: The captured state of the page (URL, screenshots, DOM data, events)
            **kwargs: Validator-specific additional context

        Returns:
            List of Finding objects. Empty list means no issues detected.
        """
        ...

    async def safe_validate(self, page_state: PageState, **kwargs) -> list[Finding]:
        """
        Wrapper around validate() that catches all exceptions.
        A validator failure should never stop the QA run.
        """
        try:
            return await self.validate(page_state, **kwargs)
        except Exception as e:
            self.logger.error(f"{self.name} validation failed: {e}", exc_info=True)
            return []
