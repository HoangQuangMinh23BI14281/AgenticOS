"""
File Facade — Large file metadata management.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import SummaryBaseFacade
from ..large_file import (
    fetch_file_record,
    fetch_files_by_conversation,
    insert_file_record,
)

if TYPE_CHECKING:
    from ....types import LargeFileRecord


class FileFacade(SummaryBaseFacade):
    """Mixin for large file operations."""

    def insert_large_file(self, **kwargs) -> LargeFileRecord:
        return self._pool.execute_write(lambda c: insert_file_record(c, **kwargs))

    def get_large_file(self, file_id: str) -> LargeFileRecord | None:
        return self._pool.execute_read(lambda c: fetch_file_record(c, file_id))

    def get_large_files_by_conversation(self, conversation_id: int) -> list[LargeFileRecord]:
        return self._pool.execute_read(lambda c: fetch_files_by_conversation(c, conversation_id))
