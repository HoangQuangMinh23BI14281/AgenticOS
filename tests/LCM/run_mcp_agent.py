
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
        # Ollama mặc định chạy ở port 11434
        self.url = "http://localhost:11434/api/chat" 

    async def chat(self, messages: List[Dict]):
        payload = {
            "model": self.model_name,
            "messages": messages,
            "stream": False  # Tắt stream để lấy nguyên cục response cho dễ xử lý
        }
        
        async with httpx.AsyncClient() as client:
            # Gửi tin nhắn thực sự cho AI
            response = await client.post(self.url, json=payload, timeout=1000.0)
            if response.status_code == 200:
                result = response.json()
                return result['message']['content'] # Đây mới là câu trả lời của AI
            else:
                return f"Lỗi gọi AI: {response.text}"

# MCP Bridge (Simple way to call our LCM MCP server via stdio)
class LcmMcpBridge:
    def __init__(self):
        pass

    async def call_tool(self, tool_name: str, args: Dict):
        from agenticos.layer1_memory.LCM.lcm_mcp import lcm_grep, lcm_expand, lcm_describe
        import inspect
        
        tool_fn = {
            "lcm_grep": lcm_grep,
            "lcm_expand": lcm_expand,
            "lcm_describe": lcm_describe
        }.get(tool_name)
        
        if not tool_fn: return "Tool not found."
        
        if inspect.iscoroutinefunction(tool_fn):
            return await tool_fn(**args)
        else:
            return tool_fn(**args)

async def main():
    agent = SmallModelAgent("qwen3.5:2b")
    mcp = LcmMcpBridge()
    
    print("--- Model-Agnostic MCP Agent (vLLM/Ollama + LCM) ---")
    user_query = "Tìm tóm tắt sâu nhất về 'Harry Potter', kiểm tra metadata của nó và lấy nội dung gốc chi tiết."
    print(f"User: {user_query}")
    
    # 1. Search using LCM tool
    print("\n[Agent] Step 1: Searching memory history...")
    search_results = await mcp.call_tool("lcm_grep", {"query": "Harry Potter"})
    print(f"[Observation] Grep results (first 100 chars): {search_results[:100]}...")

    # Simulating agent picking an ID (usually 'sum_1' in a fresh stress test)
    target_id = "sum_1" 
    if "ID: " in search_results:
        # Crude extraction for the demo
        target_id = search_results.split("ID: ")[1].split("]")[0]

    # 2. Get Metadata
    print(f"\n[Agent] Step 2: Describing node '{target_id}' to check depth/lineage...")
    metadata = await mcp.call_tool("lcm_describe", {"item_id": target_id})
    print(f"[Observation] Metadata:\n{metadata}")

    # 3. Expand Summary
    print(f"\n[Agent] Step 3: Expanding summary '{target_id}' for lossless content...")
    full_content = await mcp.call_tool("lcm_expand", {"item_id": target_id, "query": "Harry Potter"})
    print(f"[Observation] Expanded Content (first 100 chars): {full_content[:100]}...")

    # 4. Final response using all gathered context (Prompt Dressing & Guardrails)
    prompt = f"""
Bạn là một trợ lý AI thông minh đang truy xuất thông tin từ hệ thống Lossless Context Management (LCM).

--- DỮ LIỆU ĐÃ TRUY XUẤT ---
Nội dung chi tiết (Từ Memory Node {target_id}):
{full_content}

Thông tin bổ sung (Lưu ý cho AI, không nói lại với user):
- Đây là dữ liệu gốc (không phải tóm tắt).
- Độ dài: {len(full_content) // 4} tokens (ước tính).
-----------------------------

CÂU HỎI CỦA NGƯỜI DÙNG: 
{user_query}

CHỈ THỊ QUAN TRỌNG:
1. TRỰC TIẾP trả lời câu hỏi của người dùng dựa trên "Nội dung chi tiết" ở trên.
2. KHÔNG BAO GIỜ liệt kê, giải thích hay nhắc đến các thông số kỹ thuật (như id, depth, srcTok, child_manifest, metadata). Người dùng không cần biết hệ thống nội bộ hoạt động ra sao.
3. Nếu dữ liệu quá ngắn, hãy tóm tắt những gì bạn thấy và nói rõ là dữ liệu chỉ có đến vậy, không suy diễn thêm (ví dụ: không suy diễn tác giả).
"""
    messages = [{"role": "user", "content": prompt}]
    
    print("\n[Agent] Final Thinking...")
    answer = await agent.chat(messages)
    print(f"\nAI Answer: {answer}")

if __name__ == "__main__":
    asyncio.run(main())
