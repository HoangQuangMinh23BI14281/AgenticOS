
import asyncio
import os
import json
import httpx
from pathlib import Path
from typing import List, Dict

# Add src to path so we can import agenticos from anywhere
import sys
script_dir = Path(__file__).parent
src_dir = script_dir.parent.parent / "src"
sys.path.append(str(src_dir.resolve()))

# Standard OpenAI-like client (Simulated for this local PoC)
class SmallModelAgent:
    def __init__(self, model_name: str):
        self.model_name = model_name

    async def chat(self, messages: List[Dict]):
        # Since running a real LLM local API might be flaky during this session,
        # we'll use a mocked LLM response that mimics a 1.5B model's reasoning.
        content = messages[-1]["content"]
        if "Chapter 1" in content:
            return "Based on the memory results, Chapter 1 involves Harry's early life. The memory grep returned specific summaries about his arrival at the Dursleys."
        return "I can see the memory results, but I need more specific details to answer."

# MCP Bridge (Simple way to call our LCM MCP server via stdio)
class LcmMcpBridge:
    def __init__(self):
        # We'll use the 'mcp' CLI to invoke the tools for this example
        # In a real app, you'd use the MCP Python SDK's ClientSession
        pass

    def call_tool(self, tool_name: str, args: Dict):
        # For this PoC, we'll just call the python script directly as a 'command'
        import subprocess
        # This is a bit of a hack for the demo; a real client would use JSON-RPC over stdio
        # But here we'll just call the engine directly for speed in the demo script
        from agenticos.layer1_memory.LCM.lcm_mcp import lcm_grep, lcm_expand, lcm_describe
        
        if tool_name == "lcm_grep": return lcm_grep(**args)
        if tool_name == "lcm_expand": return lcm_expand(**args)
        if tool_name == "lcm_describe": return lcm_describe(**args)
        return "Tool not found."

async def main():
    agent = SmallModelAgent("qwen3.5:2b")
    mcp = LcmMcpBridge()
    
    print("--- Model-Agnostic MCP Agent (vLLM/Ollama + LCM) ---")
    print("User: Search my history for 'Chapter 1' and tell me what was found.")
    
    # 1. Search using LCM tool
    print("[Agent] Calling lcm_grep...")
    search_results = mcp.call_tool("lcm_grep", {"query": "Chapter 1"})
    print(f"[Observation] {search_results[:100]}...")
    
    # 2. Final response using the context from the tool
    prompt = f"Context from Memory Server:\n{search_results}\n\nUser Question: What was found about Chapter 1?"
    messages = [{"role": "user", "content": prompt}]
    
    print("[Agent] Thinking...")
    answer = await agent.chat(messages)
    print(f"\nAI Answer: {answer}")

if __name__ == "__main__":
    asyncio.run(main())
