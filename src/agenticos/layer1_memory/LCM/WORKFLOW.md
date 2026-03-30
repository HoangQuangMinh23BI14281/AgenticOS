# LCM Master Workflow: The Unified Technical Lifecycle

Tài liệu này là bản đặc tả quy trình vận hành (Workflow) cao nhất của hệ thống Lossless Context Management (LCM). Nó hợp nhất toàn bộ logic từ 5 tầng kiến trúc, bao gồm cả các thành phần xử lý ngoại lệ và cơ chế phục hồi dữ liệu.

## 1. Unified Master Sequence Diagram

Sơ đồ dưới đây mô tả toàn bộ vòng đời của dữ liệu: Từ khởi tạo DB, nạp tin nhắn (xử lý file lớn), nén đệ quy đa cấp, cho đến khi Agent truy xuất tìm kiếm chuyên sâu.

```mermaid
sequenceDiagram
    autonumber
    
    box "Access & Interface (MCP)" #f9f9f9
        participant AI as Agent / Model Host
        participant MCP as MCP Bridge (lcm_mcp.py)
    end
    
    box "Core Orchestration" #f0f7ff
        participant Eng as Engine (engine.py)
        participant Disp as File Dispatcher (file_dispatcher.py)
        participant Proc as Processor (processor.py)
        participant Ass as Assembler (assembler.py)
    end
    
    box "Compaction Engine" #fffbe6
        participant Coord as Coordinator (coordinator.py)
        participant Pass as Passes (passes.py)
        participant Sum as Summarizer (summarize.py)
    end
    
    box "Storage & Data Topology" #f6ffed
        participant Store as SummaryStore (Facades)
        participant DB as SQL DB (connection.py)
    end

    Note over Eng, DB: [BOOTSTRAP] Database & Migration Lifecycle
    Eng->>DB: ConnectionPool.init(WAL=Normal, Synchronous=Normal)
    Eng->>DB: run_lcm_migrations()
    activate DB
    Note over DB: Tier 1: Core Schema | Tier 2: FTS5 Tables | Tier 3: Sync Triggers
    DB-->>Eng: System Ready (Migrations complete)
    deactivate DB

    Note over AI, DB: [PHASE 1: INGESTION] Normal & Large File Handling
    AI->>Eng: add_message(role, content)
    activate Eng
    Eng->>Eng: Acquire Async Lock (session_id)
    Eng->>Proc: process_message_parts(content)
    
    rect rgb(255, 230, 230)
    Note over Proc, Disp: [EXCEPTION] Handle Massive Files/Logs
    alt Tokens > large_file_token_threshold
        Proc->>Disp: scan_and_externalize(message_id)
        activate Disp
        Disp->>Sum: summarize(massive_blob, mode=brief_tldr)
        Sum-->>Disp: TL-DR Summary
        Disp->>DB: INSERT INTO large_files (URI, Metadata)
        Disp->>DB: UPDATE messages SET content = '[Pointer + TL-DR]'
        Disp-->>Eng: Externalized (Tokens Reduced)
        deactivate Disp
    else Normal Size
        Proc-->>Eng: Normal Data Parts (Structured)
    end
    end
    
    Eng->>DB: execute_in_transaction()
    DB->>DB: INSERT INTO messages/message_parts
    Eng->>Store: append_message_to_context()
    Store->>DB: INSERT INTO context_items (Active Window)
    DB-->>Eng: Transaction Committed
    deactivate Eng

    Note over Eng, DB: [PHASE 2: RECURSIVE MAINTENANCE] Convergence Loop
    Eng->>Coord: compact_until_under(budget_limit)
    activate Coord
    loop While current_tokens > threshold (55% budget)
        Coord->>Coord: Find Candidates (Leaf OR Condensed)
        
        alt Node Depth = 0 (Leaf Pass)
            Coord->>Pass: execute_leaf_pass()
            Pass->>Sum: summarize(raw_messages, mode=leaf)
        else Node Depth > 0 (Condensed Pass / Recursive)
            Coord->>Pass: execute_condensed_pass()
            Pass->>Sum: summarize(existing_summaries, mode=recursive)
        end
        
        activate Sum
        rect rgb(255, 255, 200)
        Note over Sum: [ERROR HANDLING] Summarizer Resilience
        alt Normal LLM Synthesis
            Sum->>AI: LLM Synthesis Request
            AI-->>Sum: Raw Summary
        else LLM Crash / Timeout / Auth Error
            Sum->>Sum: deterministic_fallback_summary()
            Note right of Sum: [Safety Net] Truncation + Preamble concat
        end
        end
        
        Sum->>Sum: [Bloat Guard] & [Junk Guard] Checks
        Sum-->>Pass: Verified Summary Node
        deactivate Sum
        
        Pass->>Store: insert_summary_and_replace_context_atomic()
        activate Store
        Store->>DB: [ATOMIC SWAP] BEGIN TRANSACTION
        Store->>DB: INSERT INTO summaries (Create Node + DAG Lineage)
        Store->>DB: DELETE range + INSERT summary + SHIFT ordinals
        Store->>DB: COMMIT
        Store->>Store: Invalidate caches (TTLCache)
        deactivate Store
        
        Pass-->>Coord: Summary ID
        Coord->>Coord: Recalculate context_tokens
    end
    deactivate Coord

    Note over AI, DB: [PHASE 3: RETRIEVAL] Search -> Describe -> Expand
    AI->>Ass: get_assembled_context()
    Ass->>DB: FETCH active items from context_items
    DB-->>Ass: (Summaries, Recent Messages)
    Ass->>Ass: Inject XML Hooks & [lcm_expand] Instructions
    Ass-->>AI: Prompt with "Compressed" Memory

    Note over AI, DB: [AGENT TOOLCHAIN] Discovery & High-Fidelity Recall
    AI->>MCP: call_tool: lcm_grep(query)
    MCP->>Eng: grep(query)
    Eng->>DB: FTS5 Full-text Search (messages + summaries)
    DB-->>AI: List of Results with [Depth Labels]

    opt Agent needs to Evaluate Metadata
        AI->>MCP: call_tool: lcm_describe(item_id)
        MCP->>Eng: describe(item_id)
        Eng->>Store: get_summary_record()
        Note right of Eng: [SCHEMA: NodeSummaryMetadata]
        Store-->>AI: {id, depth, srcTok, descTok, child_manifest}
    end

    opt Agent needs Original Data (Verbatim)
        AI->>MCP: call_tool: lcm_expand(item_id)
        MCP->>Eng: expand(item_id)
        Eng->>Store: get_subtree(item_id)
        activate Store
        Store->>DB: [RECURSIVE CTE] fetch_subtree()
        DB-->>Store: Hierarchy DAG (Level 0 to N)
        Store-->>Eng: Verbatim Message list
        deactivate Store
        Eng-->>AI: [Lossless Content] Verbatim History
    end
```

---

## 2. Cấu trúc dữ liệu (Object Schemas)

Để Agent có thể suy luận chính xác, hệ thống cung cấp dữ liệu theo các Schema chuẩn hóa sau:

### 2.1 Node Metadata (Kết quả `lcm_describe`)
```json
{
  "id": "sum_8a2b3c4d",
  "depth": 2,           // Cấp độ nén (0: Gốc, 1: Tóm tắt cấp 1, 2+: Đệ quy)
  "srcTok": 12450,      // Tổng số token gốc "ẩn" dưới node này
  "descTok": 1500,      // Dung lượng token thực tế của các node con trực tiếp
  "content_preview": "Hội thoại về thiết kế hệ thống...",
  "child_manifest": [   // Danh mục tóm tắt cấp dưới giúp Agent chọn luồng expand
    {"id": "sum_1", "tokens": 400, "depth": 1},
    {"id": "sum_2", "tokens": 350, "depth": 1}
  ]
}
```

### 2.2 Expansion Grant
Khi thực hiện `lcm_expand`, nếu dữ liệu quá lớn, hệ thống sẽ cấp một quyền ủy quyền (`DelegationGrant`) để Sub-agent thực hiện trích lọc thông tin.

---

## 3. Chiến lược Xử lý lỗi (Error Handling & Resilience)

LCM được thiết kế với tư duy "Fail-safe", đảm bảo bộ nhớ không bao giờ bị korrupt dù AI Engine gặp lỗi:

1.  **AI Summarizer Failure**: Khi LLM không thể tóm tắt (timeout/crash), hệ thống kích hoạt `deterministic_fallback_summary()`. Phương pháp này sử dụng thuật toán cắt tỉa (Sliding Window) để giữ lại 30% đầu và 30% cuối của văn bản gốc, nối lại bằng thẻ `[...]`. **Kết quả**: Luôn có tóm tắt để giải phóng RAM, dù chất lượng nội dung có thể giảm nhẹ.
2.  **The Bloat Guard**: Nếu LLM "nói hươu nói vượn" dẫn đến bản tóm tắt dài hơn cả văn bản thô, hệ thống sẽ tự động vứt bỏ (Discard) kết quả nén đó và thử lại với `aggressive=True` hoặc dùng fallback.
3.  **Atomic Integrity**: Mọi thao tác thay đổi cấu trúc Context Window đều nằm trong `BEGIN IMMEDIATE TRANSACTION`. Nếu ổ đĩa đầy hoặc lỗi SQL, toàn bộ quy trình sẽ ROLLBACK về trạng thái trước khi nén để tránh việc mất ký ức của người dùng.
4.  **Chaos Guard**: Tại tầng Processor, bất kỳ tin nhắn nào vượt quá 50,000 ký tự sẽ bị ép buộc cắt tỉa hoặc đẩy sang File Dispatcher ngay lập tức để bảo vệ tài nguyên tính toán token.

---
*Bản đặc tả Master Workflow này là tài liệu tham chiếu chính thức cho toàn bộ module LCM trong AgenticOS.*
