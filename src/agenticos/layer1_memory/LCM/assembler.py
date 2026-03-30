"""
LCM Assembler — Logic for context hydration and prompt assembly.
"""

from __future__ import annotations

import logging
from typing import Any

from .types import ContextItemRecord, ContextItemType, MessageRecord, SummaryRecord

logger = logging.getLogger("lcm.assembler")


class LcmAssembler:
    """
    Handles context assembly:
    - Building the <lcm_metadata> XML block.
    - Formatting the history sequence with Active Memory cues.
    """

    def assemble_context_history(
        self, 
        bindles: list[SummaryRecord], 
        messages: list[MessageRecord]
    ) -> str:
        """
        Merge Active Summaries and Recent Messages into a single prompt block.
        """
        history_pieces = []
        
        if bindles:
            history_pieces.append("--- ACTIVE MEMORY SUMMARIES ---")
            history_pieces.append(
                "The following are condensed summaries of older conversations. "
                "Use 'lcm_expand' if you need details."
            )
            for b in bindles:
                history_pieces.append(f"[SUMMARY D{b.depth} | ID: {b.summary_id}]: {b.content}")
            history_pieces.append("--- END SUMMARIES ---")

        if messages:
            for msg in messages:
                history_pieces.append(f"{msg.role.value.capitalize()}: {msg.content}")

        history_text = "\n\n".join(history_pieces)
        
        # Build Metadata
        header = self.build_metadata_hooks(bindles)
        
        return f"{header}\n\n<context_history>\n{history_text}\n</context_history>"

    def build_metadata_hooks(self, bindles: list[SummaryRecord]) -> str:
        """
        Build the <lcm_metadata> XML block for model cue injection.
        """
        if not bindles:
             return (
                 "<lcm_metadata>\n"
                 "  <state>Empty Context</state>\n"
                 "</lcm_metadata>"
             )

        xml = ["<lcm_metadata>"]
        xml.append("  <active_summaries> <!-- 'Bindles' currently in RAM -->")
        for b in bindles:
            # Clean snippet for topic attribute
            snippet = (b.content[:60]
                       .replace("\n", " ")
                       .replace('"', "'")
                       .strip() + "...")
            xml.append(
                f'    <summary id="{b.summary_id}" depth="{b.depth}" tokens="{b.token_count}" '
                f'topic="{snippet}">Depth {b.depth} condensed memory</summary>'
            )
        xml.append("  </active_summaries>")
        xml.append(
            "  <instruction>\n"
            "    You are an agent with LCM (Lossless Context Management) memory.\n"
            "    Active memories (summaries) are provided below in the <context_history> block.\n"
            "    CRITICAL: If you encounter a [SUMMARY] node and need deeper historical details,\n"
            "    you MUST call `lcm_expand(item_id=...)` to retrieve the original lossless data.\n"
            "    Do not hallucinate details that are not explicitly in the summary; use your tools.\n"
            "  </instruction>"
        )
        xml.append("</lcm_metadata>")

        return "\n".join(xml)
