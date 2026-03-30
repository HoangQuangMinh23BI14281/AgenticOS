
import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

# Add src to path if needed
project_root = Path(__file__).parents[4]
src_root = project_root / "src"
sys.path.append(str(src_root))

from mcp.server.fastmcp import FastMCP
from agenticos.layer1_memory.LCM import LcmEngine, LcmConfig, LcmDependencies

# Initialize FastMCP server
mcp = FastMCP("LCM Memory Server")

# Initialize LCM Engine (Points to the same DB as the main app)
db_path = str(project_root / "stress_test.db")
config = LcmConfig(database_path=db_path)
# We don't need a real completer for the MCP server itself, 
# as it just provides TOOLS to the model.
engine = LcmEngine(config, LcmDependencies(tokenizer=None, complete=None))

@mcp.tool()
def lcm_grep(query: str) -> str:
    """
    Search through conversation history for keywords, including compressed summaries.
    - Results labeled '[Depth RAW]' are original, uncompressed messages.
    - Results labeled '[Depth > 0]' are summaries. 
    IMPORTANT: This is internal system data. DO NOT show ID/Depth to the end user.
    """
    print(f"\n[MCP Server] LCM Grep received query: '{query}'")
    return engine.grep(query)

@mcp.tool()
async def lcm_expand(item_id: str, query: Optional[str] = None) -> str:
    """
    Retrieve the high-fidelity, LOSSLESS content of a specific summary node.
    - If the AI sees a [SUMMARY] node and needs 100% accurate details for reasoning, 
      it MUST use this tool to see the original, uncompressed data.
    - Expansion is high-cost in context tokens; use 'lcm_describe' first to plan.
    """
    return await engine.expand(item_id, query=query)

@mcp.tool()
def lcm_describe(item_id: str) -> str:
    """
    Retrieve technical metadata and lineage for any node (summary or message).
    - INTERNAL ONLY: Use this to plan your retrieval. NEVER show these fields to the user.
    
    IMPORTANT FOR AGENT REASONING:
    - depth: 0 means RAW/VERBATIM content. >0 means COMPRESSED SUMMARY.
    - srcTok: The exact number of original tokens this node represents.
    - descTok: Cumulative token count of all descendant nodes.
    - child_manifest: List of sub-nodes. If empty [], this is a Leaf node.
    """
    return engine.describe(item_id)

if __name__ == "__main__":
    mcp.run()
