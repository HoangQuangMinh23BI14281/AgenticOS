
import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

# Add src to path if needed
root = Path(__file__).parent.parent.parent.parent
sys.path.append(str(root))

from mcp.server.fastmcp import FastMCP
from agenticos.layer1_memory.LCM import LcmEngine, LcmConfig, LcmDependencies

# Initialize FastMCP server
mcp = FastMCP("LCM Memory Server")

# Initialize LCM Engine (Points to the same DB as the main app)
db_path = os.path.join(os.getcwd(), "stress_test.db")
config = LcmConfig(database_path=db_path)
# We don't need a real completer for the MCP server itself, 
# as it just provides TOOLS to the model.
engine = LcmEngine(config, LcmDependencies(tokenizer=None, complete=None))

@mcp.tool()
def lcm_grep(query: str) -> str:
    """
    Search through conversation history for keywords, including compressed summaries.
    Returns a list of matching messages and summaries with their IDs.
    """
    results = engine.summaries.search_summaries(query)
    if not results:
        return f"No results found for '{query}'"
    
    output = []
    for r in results:
        item_type = "Summary" if r.get("summary_id") else "Message"
        item_id = r.get("summary_id") or r.get("message_id")
        content = r.get("content", "")[:200] + ("..." if len(r.get("content", "")) > 200 else "")
        output.append(f"[{item_type} | ID: {item_id}]\nContent: {content}\n")
    
    return "\n".join(output)

@mcp.tool()
def lcm_expand(item_id: str, query: Optional[str] = None) -> str:
    """
    Retrieve the full, lossless content of a specific summary node.
    If the AI encounters a [SUMMARY] node, it MUST use this tool to see original details.
    """
    # Check if it's a summary or message
    if item_id.startswith("sum_"):
        result = engine.summaries.expand_summary(item_id, query=query)
    else:
        # Fallback to direct message fetch if needed
        # (Though usually expand is for summaries)
        msg_id = int(item_id) if item_id.isdigit() else None
        if msg_id:
            msg = engine.conversations.get_message_by_id(msg_id)
            result = msg.content if msg else "Message not found."
        else:
            result = "Invalid ID format."
            
    return str(result)

@mcp.tool()
def lcm_describe(item_id: str) -> str:
    """
    Get technical lineage and metadata for any node (summary or message) in history.
    Helps understand the context structure.
    """
    if item_id.startswith("sum_"):
        s = engine.summaries.get_summary(item_id)
        if not s: return "Summary not found."
        parents = engine.summaries.get_summary_parents(item_id)
        return (
            f"Summary ID: {s.summary_id}\n"
            f"Depth: {s.depth}\n"
            f"Tokens: {s.token_count}\n"
            f"Source Tokens: {s.source_message_token_count}\n"
            f"Parents: {', '.join(parents) if parents else 'None (Leaf)'}\n"
            f"Topic/Desc: {s.metadata.get('topic', 'N/A')}\n"
        )
    else:
        msg_id = int(item_id) if item_id.isdigit() else None
        if not msg_id: return "Invalid ID format."
        msg = engine.conversations.get_message_by_id(msg_id)
        if not msg: return "Message not found."
        return f"Message ID: {msg.message_id}\nRole: {msg.role}\nTokens: {msg.token_count}\nContent: {msg.content[:100]}..."

if __name__ == "__main__":
    mcp.run()
