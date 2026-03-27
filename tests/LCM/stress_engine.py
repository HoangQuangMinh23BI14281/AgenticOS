
import asyncio
from agenticos.layer1_memory.LCM import MessageRole

async def stress_loop(engine, cmpl, state):
    session_id = "stress_session"
    
    PROMPTS = [
        "What is Lossless Context Management?", "Explain SQLite WAL mode in LCM.", 
        "How do you implement asyncio locks?", "What are Leaf passes?",
        "What are Condensed passes?", "Summarize the entire concept concisely."
    ]
    
    turn = 0
    while state["running"]:
        p = PROMPTS[turn % len(PROMPTS)]
        turn += 1
        
        # Ingest User Message
        engine.ingest_message(session_id, MessageRole.USER, [{"text": p}])
        
        # Assemble context and call LLM
        ctx = engine.assemble(session_id, context_budget= state["budget"])
        ai_res = await cmpl.complete(
            model="deepseek-r1:1.5b", 
            messages=[{"role": "user", "content": f"{ctx}\nRespond to: {p}"}], 
            max_tokens= 600
        )
        
        ai_text = ai_res.content[0].text if (ai_res.content and hasattr(ai_res.content[0], 'text')) else (
            ai_res.content[0] if (ai_res.content and isinstance(ai_res.content[0], str)) else "[OLLAMA CONNECTION FAILED / TIMEOUT]"
        )
        if "</think>" in ai_text: 
            ai_text = ai_text.split("</think>")[-1].strip()
        
        # Ingest AI Response
        ai_msg = engine.ingest_message(session_id, MessageRole.ASSISTANT, [{"text": ai_text}])
        print(f"[TEST] Persisted AI Message {ai_msg.message_id}: {ai_text[:50]}...")
        
        # Maintenance check
        c = engine.conversations.get_conversation_by_session_id(session_id)
        if c and engine.summaries.get_context_token_count(c.conversation_id) > state["threshold"]:
            await engine.run_maintenance(session_id, context_budget=state["budget"])
            
        await asyncio.sleep(1.0)
    
    state["running"] = False
