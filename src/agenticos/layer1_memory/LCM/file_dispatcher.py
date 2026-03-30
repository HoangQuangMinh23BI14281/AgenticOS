"""
Large File Dispatcher — Externalizes bloated message parts into storage.

Ported from file-dispatcher.ts concepts. Prevents Agent's active context
from being dominated by huge logs or diffs.
"""

from __future__ import annotations

import logging
from uuid import uuid4

from .config import LcmConfig
from .store import ConversationStore, SummaryStore
from .summarize import LcmSummarizer
from .tokenizer_util import LcmSimpleTokenizer

logger = logging.getLogger("lcm.file_dispatcher")


class FileDispatcher:
    """
    Scans recent messages for large files/outputs and externalizes them.
    Leaves a short Exploration Summary and a pointer in the DB.
    """

    def __init__(
        self,
        conversation_store: ConversationStore,
        summary_store: SummaryStore,
        summarizer: LcmSummarizer,
        config: LcmConfig,
    ) -> None:
        self._conv_store = conversation_store
        self._summary_store = summary_store
        self._summarizer = summarizer
        self._config = config

    async def scan_and_externalize(self, conversation_id: int) -> int:
        """
        Find messages exceeding large_file_token_threshold, summarize them,
        move their raw content to large_files table, and replace the message
        content in-place with a pointer and the summary.
        
        Returns the number of files externalized.
        """
        threshold = self._config.large_file_token_threshold
        
        # We only scan messages (not summaries) that are currently in context
        items = self._summary_store.get_context_items(conversation_id)
        msg_ids = [i.message_id for i in items if i.item_type.value == 'message' and i.message_id]
        
        if not msg_ids:
            return 0

        externalized_count = 0
        
        for msg_id in msg_ids:
            msg = self._conv_store.get_message_by_id(msg_id)
            if not msg or msg.token_count < threshold:
                continue
                
            # It's bloated. Externalize it.
            logger.info(
                "[lcm] externalizing large message %d (tokens=%d > limit=%d)",
                msg.message_id, msg.token_count, threshold
            )
            
            # 1. Summarize the blob
            summary = await self._summarizer.summarize(
                msg.content, 
                aggressive=True,
                custom_instructions="This is a massive file/log block being externalized. Write extremely brief TL;DR."
            )
            
            # 2. Store in large_files
            file_id = f"file_{uuid4().hex[:12]}"
            self._summary_store.insert_large_file(
                file_id=file_id,
                conversation_id=conversation_id,
                storage_uri=f"lcm://internal/{file_id}",
                file_name=f"auto_externalized_msg_{msg_id}.txt",
                mime_type="text/plain",
                byte_size=len(msg.content.encode('utf-8')),
                exploration_summary=summary
            )
            
            # 3. Replace message content in-place with pointer
            new_content = (
                f"[LCM File: {file_id}]\n"
                f"Exploration Summary:\n{summary}\n\n"
                f"Use `lcm_expand` on {file_id} if you absolutely need the massive raw contents."
            )
            
            # Update the DB via official Store method
            new_tokens = len(LcmSimpleTokenizer().encode(new_content))
            self._conv_store.update_message(msg_id, new_content, new_tokens)
            
            externalized_count += 1
            
        return externalized_count
            
        return externalized_count
