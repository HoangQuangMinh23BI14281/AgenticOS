"""
LCM DAG Node — Integration layer connecting the database with the active DAG.

Acts as the Python equivalent of MemoryNode in the TypeScript architecture, 
enabling the Agent to walk the graph in memory.
"""

from __future__ import annotations

from typing import Any
from .engine import LcmEngine
from .types import SummaryRecord

class LcmNode:
    """
    Represents a specific Memory Node (either a Summary or Message) 
    in the Directed Acyclic Graph.
    
    Provides fluent methods to traverse parents/children lazily via the DB.
    """
    def __init__(self, node_id: str, engine: LcmEngine, is_message: bool = False):
        self.id = node_id
        self._engine = engine
        self._is_message = is_message
        self._data: SummaryRecord | dict | None = None
        self._fetched = False

    def load(self) -> None:
        """Lazily load the node data from the database."""
        if self._fetched:
            return
            
        if self._is_message:
            # message nodes
            if self.id.isdigit():
                msg = self._engine.conversations.get_message_by_id(int(self.id))
                if msg:
                    parts = self._engine.conversations.get_message_parts(int(self.id))
                    self._data = {"message": msg, "parts": parts}
        else:
            # summary nodes
            self._data = self._engine.summaries.get_summary(self.id)
            
        self._fetched = True

    @property
    def is_valid(self) -> bool:
        self.load()
        return self._data is not None

    @property
    def content(self) -> str:
        self.load()
        if not self._data:
            return ""
        if self._is_message:
            return getattr(self._data["message"], "content", "")
        return getattr(self._data, "content", "")

    @property
    def depth(self) -> int:
        self.load()
        if not self._data or self._is_message:
            return 0
        return getattr(self._data, "depth", 0)

    def get_parents(self) -> list[LcmNode]:
        """Fetch nodes that this node was condensed FROM."""
        if self._is_message:
            return []
            
        parents = self._engine.summaries.get_summary_parents(self.id)
        return [LcmNode(p.summary_id, self._engine, is_message=False) for p in parents]

    def get_children(self) -> list[LcmNode]:
        """Fetch nodes that this node was condensed INTO."""
        if self._is_message:
            # Messages might map to leaf summaries
            refs = self._engine.summaries._pool.execute_read(
                lambda conn: conn.execute(
                    "SELECT summary_id FROM summary_messages WHERE message_id = ?", 
                    (int(self.id),)
                ).fetchall()
            )
            return [LcmNode(r["summary_id"], self._engine, is_message=False) for r in refs]
            
        children = self._engine.summaries.get_summary_children(self.id)
        return [LcmNode(c.summary_id, self._engine, is_message=False) for c in children]

    def get_source_messages(self) -> list[LcmNode]:
        """Fetch raw leaf messages underlying this node."""
        if self._is_message:
            return [self]
            
        msg_ids = self._engine.summaries.get_summary_messages(self.id)
        return [LcmNode(str(mid), self._engine, is_message=True) for mid in msg_ids]
        
    def to_dict(self) -> dict[str, Any]:
        self.load()
        if not self._data:
            return {"id": self.id, "error": "Not Found"}
            
        if self._is_message:
            m = self._data["message"]
            return {
                "id": self.id,
                "type": "message",
                "role": m.role.value,
                "tokens": m.token_count,
                "content": self.content[:200] + ("..." if len(self.content) > 200 else "")
            }
            
        return {
            "id": self.id,
            "type": "summary",
            "kind": self._data.kind.value,
            "depth": self.depth,
            "tokens": self._data.token_count,
            "descendants": self._data.descendant_count,
            "content": self.content[:200] + ("..." if len(self.content) > 200 else "")
        }

    def __repr__(self) -> str:
        typ = "Msg" if self._is_message else "Sum"
        return f"<LcmNode {typ} {self.id}>"
