import os
import logging
from abc import ABC, abstractmethod
from datetime import datetime

logger = logging.getLogger(__name__)


class BaseExportFlow(ABC):
    LABEL = "Base Export"
    DESCRIPTION = "Override in subclass"

    SELECTORS = {}

    def __init__(self):
        self.page = None
        self.context = None

    def set_page(self, page, context):
        self.page = page
        self.context = context

    @abstractmethod
    async def navigate_to_export_screen(self):
        pass

    @abstractmethod
    async def apply_filters(self, params: dict | None = None):
        pass

    @abstractmethod
    async def trigger_export(self) -> str:
        pass

    @abstractmethod
    def validate_download(self, file_path: str) -> bool:
        pass

    async def post_process(self, file_path: str, metadata: dict) -> dict:
        return {}

    def get_expected_extensions(self) -> list:
        return ['.xlsx', '.csv', '.txt', '.zip']
