
import asyncio
import dataclasses
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel

# Add src to path
root = Path(__file__).parent.parent.parent
sys.path.append(str(root / "src"))

from agenticos.layer1_memory.LCM import LcmEngine, LcmConfig, LcmDependencies, MessageRole
from agenticos.layer1_memory.LCM.store.summary.crud import sanitize_summary_content

# Local imports
from .templates import HTML_PAGE
from .mock_llm import SimpleTokenizer, OllamaCompleter

app = FastAPI()
state = {"running": False, "budget": 8000, "threshold": 4000, "session_id": "chat_session"}
db_path = "stress_test.db"

# Engine setup
config = LcmConfig(database_path=db_path, context_threshold=0.5)
cmpl = OllamaCompleter()
engine = LcmEngine(config, LcmDependencies(tokenizer=SimpleTokenizer(), complete=cmpl.complete))

class ChatRequest(BaseModel):
    query: str

@app.get("/")
def get_index(): 
    return HTMLResponse(HTML_PAGE)

@app.post("/chat")
async def handle_chat(req: ChatRequest):
    session_id = state["session_id"]
    
    # 1. [Standardized API] Ingest User Message
    await engine.add_message(session_id, MessageRole.USER, req.query)
    
    # 2. [Standardized API] Assemble context and call LLM
    ctx = engine.get_assembled_context(session_id, context_budget= state["budget"])
    ai_res = await cmpl.complete(
        messages=[{"role": "user", "content": f"{ctx}\n\nRespond to: {req.query}"}], 
        max_tokens= 4096
    )
    
    ai_text = ai_res.content[0].text if (ai_res.content and hasattr(ai_res.content[0], 'text')) else (
        ai_res.content[0] if (ai_res.content and isinstance(ai_res.content[0], str)) else "[OLLAMA CONNECTION FAILED]"
    )
    # [Clean CoT/Leaks]
    ai_text = sanitize_summary_content(ai_text)
    
    # 3. [Standardized API] Ingest AI Response
    await engine.add_message(session_id, MessageRole.ASSISTANT, ai_text)
    
    # 4. [Standardized API] Check maintenance
    conv = engine.conversations.get_conversation_by_session_id(session_id)
    if conv and engine.summaries.get_context_token_count(conv.conversation_id) > state["threshold"]:
        await engine.process_maintenance(session_id, context_budget=state["budget"])
    
    return {"status": "ok", "response": ai_text}

@app.post("/v1/chat/completions")
async def openai_completions(req: dict):
    # Mock OpenAI response for qwen_agent and other libraries
    messages = req.get("messages", [])
    if not messages: return {"error": "No messages"}
    
    session_id = state["session_id"]
    conv = engine.conversations.get_or_create_conversation(session_id)
    
    # Lấy số lượng tin nhắn hiện tại trong DB để so sánh
    existing_count = engine.conversations.get_message_count(conv.conversation_id)
    
    # Chỉ ingest những tin nhắn "mới" từ cuối danh sách messages
    new_messages = []
    if len(messages) > existing_count:
        new_messages = messages[existing_count:]
        
    for m in new_messages:
        role_map = {
            "user": MessageRole.USER,
            "assistant": MessageRole.ASSISTANT,
            "system": MessageRole.SYSTEM,
            "tool": MessageRole.ASSISTANT
        }
        role = role_map.get(m["role"], MessageRole.USER)
        # [Standardized API]
        await engine.add_message(session_id, role, str(m["content"]))

    # Assemble context từ LCM (Lúc này đã bao gồm cả các tin cũ được nén)
    # [Standardized API]
    ctx = engine.get_assembled_context(session_id, context_budget=state["budget"])
    
    # Lấy câu hỏi cuối cùng để LLM tập trung trả lời
    last_query = messages[-1]["content"] if messages[-1]["role"] == "user" else "Continue the conversation based on history."
    
    ai_res = await cmpl.complete(
        messages=[{"role": "user", "content": f"{ctx}\n\nUser: {last_query}"}], 
        max_tokens=4096
    )
    
    ai_text = ai_res.content[0].text if (ai_res.content and hasattr(ai_res.content[0], 'text')) else (
        ai_res.content[0] if (ai_res.content and isinstance(ai_res.content[0], str)) else ""
    )
    
    return {
        "id": f"chatcmpl-{uuid4().hex[:8]}",
        "object": "chat.completion",
        "created": 123456789,
        "model": "qwen3.5:2b",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": ai_text},
            "finish_reason": "stop"
        }]
    }

@app.get("/grep")
def grep_tool(q: str):
    conv = engine.conversations.get_conversation_by_session_id(state["session_id"])
    conv_id = conv.conversation_id if conv else None
    msg_hits = engine.conversations.search_messages(q, "full_text", conv_id, 15)
    sum_hits = engine.summaries.search_summaries(q, "full_text", conv_id, 15)
    return {"summaries": sum_hits, "messages": msg_hits}

@app.get("/describe")
def describe_tool(id: str):
    s = engine.summaries.get_summary(id)
    if s:
        children = engine.summaries.get_summary_children(id)
        return {
            "type": "summary", "id": s.summary_id, "depth": s.depth, 
            "tokens": s.token_count, "descTok": s.descendant_token_count, 
            "srcTok": s.source_message_token_count, "content": s.content,
            "children": [{"id": c.summary_id, "tokens": c.token_count} for c in children]
        }
    m = engine.conversations.get_message_by_id(int(id)) if id.isdigit() else None
    if m:
        return {"type": "message", "id": m.message_id, "role": m.role.value, "tokens": m.token_count, "content": m.content}
    return {"error": "Node not found"}

@app.get("/expand")
async def expand_tool(id: str, q: str = None):
    res = await engine.expand(item_id=id, query=q) 
    
    if isinstance(res, dict):
        res = json.dumps(res, indent=2)
        
    return {"result": res}

@app.get("/stream")
async def sse_stream():
    async def event_generator():
        while True:
            conv = engine.conversations.get_conversation_by_session_id(state["session_id"])
            payload = {
                "active_tokens": 0, "budget": state["budget"], "tail_msgs": 0, "tail_tokens": 0, 
                "active_summaries": [], "dag_tree": [], "total_db_messages": 0, "ollama_ok": False,
                "all_tail_msgs": []
            }
            if conv:
                payload["ollama_ok"] = await cmpl.check_health()
                payload["total_db_messages"] = engine.conversations.get_message_count(conv.conversation_id)
                payload["active_tokens"] = engine.summaries.get_context_token_count(conv.conversation_id)
                items = engine.summaries.get_context_items(conv.conversation_id)
                
                for i in items:
                    if i.item_type.value == "message":
                        m = engine.conversations.get_message_by_id(i.message_id)
                        if m: 
                            payload["tail_msgs"] += 1
                            payload["tail_tokens"] += m.token_count
                            role = "user" if m.role.value == "user" else "ai"
                            payload["all_tail_msgs"].append({"role": role, "content": m.content})
                    else:
                        s = engine.summaries.get_summary(i.summary_id)
                        if s: 
                            payload["active_summaries"].append({
                                "id": s.summary_id, "depth": s.depth, 
                                "tokens": s.token_count, "content": s.content[:100] + "..."
                            })
                
                edges = engine.summaries._pool.execute_read(lambda conn: conn.execute(
                    "SELECT summary_id, parent_summary_id FROM summary_parents"
                ).fetchall())
                
                all_summaries = engine.summaries.get_summaries_by_conversation(conv.conversation_id)
                
                mapped_nodes = []
                for s in all_summaries:
                    d = dataclasses.asdict(s)
                    d["id"] = d.pop("summary_id")
                    
                    # Correct Direction: Who are the parents of node 'd'?
                    # In DB: summary_id = Child, parent_summary_id = Parent
                    actual_parents = [
                        row["parent_summary_id"] for row in edges 
                        if row["summary_id"] == d["id"]
                    ]
                    d["parent_ids"] = actual_parents
                    mapped_nodes.append(d)
                
                payload["dag_tree"] = mapped_nodes
                        
            yield {"event": "message", "data": json.dumps(payload, default=str)}
            await asyncio.sleep(1.0)
            
    return EventSourceResponse(event_generator())