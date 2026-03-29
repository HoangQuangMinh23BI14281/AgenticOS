
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
    Returns a list of matching messages and summaries with their IDs.
    """
    print(f"\n[MCP Server] LCM Grep received query: '{query}'")
    return engine.grep(query)

@mcp.tool()
async def lcm_expand(item_id: str, query: Optional[str] = None) -> str:
    """
    Retrieve the full, lossless content of a specific summary node.
    If the AI encounters a [SUMMARY] node, it MUST use this tool to see original details.
    """
    return await engine.expand(item_id, query=query)

@mcp.tool()
def lcm_describe(item_id: str) -> str:
    """
    Get technical lineage and metadata for any node (summary or message) in history.
    Helps understand the context structure.
    """
    return engine.describe(item_id)

if __name__ == "__main__":
    mcp.run()
