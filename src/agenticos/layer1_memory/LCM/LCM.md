# Lossless Context Management (LCM) - Technical Specification

Lossless Context Management (LCM) is the core memory architecture of AgenticOS, designed to provide LLMs with a "permanent memory" that scales indefinitely while maintaining 100% data integrity on edge devices.

---

## 1. System Architecture

LCM is structured into four distinct horizontal layers, ensuring separation of concerns between storage persistence and high-level agent interaction.

```mermaid
graph TD
    subgraph Access_Layer [Access & Protocol]
        MCP[lcm_mcp.py]
        Tools[tools/ folder]
    end

    subgraph Engine_Layer [Intelligence & Control]
        Engine[engine.py]
        Compactor[compaction.py]
        Summarizer[summarize.py]
    end

    subgraph Store_Layer [Logic & Caching]
        ConvStore[conversation_store.py]
        SumStore[summary_store.py]
        FTS[fts5_sanitize.py]
    end

    subgraph DB_Layer [Persistence]
        Conn[connection.py]
        Migrator[migration.py]
        SQLite[(stress_test.db)]
    end

    Access_Layer <--> Engine_Layer
    Engine_Layer <--> Store_Layer
    Store_Layer <--> DB_Layer
```

---

## 2. Physical Data Model (Database Schema)

LCM utilizes a sophisticated 9-table schema in SQLite (managed by `migration.py`) to handle multi-modal message parts and recursive summary structures.

```mermaid
erDiagram
    conversations ||--o{ messages : "defines history"
    conversations ||--o{ summaries : "has compressed nodes"
    conversations ||--o{ context_items : "active window"
    conversations ||--o{ large_files : "external assets"
    conversations ||--|| conversation_bootstrap_state : "tracking"

    messages ||--o{ message_parts : "fragmented into"
    messages ||--o{ summary_messages : "source for (CASCADE)"

    summaries ||--o{ summary_messages : "aggregates (CASCADE)"
    summaries ||--o{ summary_parents : "as_child (CASCADE)"
    summaries ||--o{ summary_parents : "as_parent (CASCADE)"
    summaries ||--o{ context_items : "referenced_by"

    conversations {
        int conversation_id PK
        string session_id
        string session_key "unique"
        string title
        datetime created_at
    }

    messages {
        int message_id PK
        int conversation_id FK
        int seq "unique_per_conv"
        string role "user/assistant/system/tool"
        text content
        int token_count
    }

    message_parts {
        string part_id PK
        int message_id FK
        string part_type "text/reasoning/tool/patch/file/etc"
        int ordinal "seq_in_message"
        text text_content
        text metadata "JSON_blobs"
    }

    summaries {
        string summary_id PK
        int conversation_id FK
        string kind "leaf/condensed"
        int depth "DAG_level"
        text content "cleaned_summary"
        int token_count
        int source_message_token_count
        string model "generator_model"
    }

    summary_messages {
        string summary_id PK, FK "CASCADE ON DELETE"
        int message_id PK, FK "CASCADE ON DELETE"
        int ordinal
    }

    summary_parents {
        string summary_id PK, FK "CASCADE ON DELETE"
        string parent_summary_id PK, FK "CASCADE ON DELETE"
        int ordinal
    }

    context_items {
        int conversation_id PK, FK
        int ordinal PK
        string item_type "message/summary"
        int message_id FK
        string summary_id FK
    }

    large_files {
        string file_id PK
        int conversation_id FK
        string storage_uri
        string file_name
        int byte_size
        text exploration_summary
    }

    conversation_bootstrap_state {
        int conversation_id PK, FK
        string session_file_path
        int last_processed_offset
        int last_seen_size
    }
```

### 2.1 Core Tables Breakdown
*   **`conversations`**: Tracks sessions, titles, and bootstrap states.
*   **`messages`**: Source of truth for raw dialogue. Immutable once written.
*   **`message_parts`**: Highly granular storage for different content types (Text, Reasoning, Tool Calls, Patches, Files, etc.). Supports 13+ specialized part types.
*   **`summaries`**: Stores the compressed Nodes. Attributes include `depth` (DAG level), `token_count`, and `source_message_token_count`.
*   **`context_items`**: The "Active Window" pointer table. Determines what is currently visible to the LLM.
*   **`large_files`**: Registry for files externalized from the context to save VRAM.

### 2.2 Relation & DAG Tables
*   **`summary_messages`**: Links Summary Nodes to their original Raw Messages.
*   **`summary_parents`**: The backbone of the **Recursive DAG**. Links child summaries to parent summaries, enabling multi-level "zooming."

---

## 3. Compaction Lifecycle (3-Level Defense)

The `CompactionEngine` in `compaction.py` prevents "Context Rot" by triggering background compression when tokens exceed **55% of the budget**.

### The Escalation Strategy:
1.  **Level 1: Synthesis (Standard)**: Uses a small LLM to synthesize a logical summary of the oldest block in the window.
2.  **Level 2: Point Extraction (Aggressive)**: Triggered if Level 1 fails to reduce tokens significantly. Forces the LLM to output ultra-dense bullet points.
3.  **Level 3: Deterministic Truncation (Safety Fallback)**: A pure Python logic that "chops" the oldest items to guarantee context window recovery, ensuring the system never crashes due to LLM failure.

> [!TIP]
> **QA Protection (Fresh Tail Logic)**: LCM always preserves at least 6 messages (3 Q&A pairs) at the end of the history to maintain immediate conversation coherence, even during aggressive compaction.

---

## 4. Advanced Memory Access Tools

Agents interact with deep history via a set of specialized tools that bypass the active context window.

| Tool | Source | Logic |
| :--- | :--- | :--- |
| **`lcm_grep`** | `tools/lcm_grep.py` | Uses **SQLite FTS5** (Porter stemmer + unicode61) for sub-millisecond keyword searches across all history. |
| **`lcm_describe`** | `tools/lcm_describe.py` | Provides metadata about a Summary Node (timeframe, count, depth) so the Agent can decide if it needs to see the full content. |
| **`lcm_expand`** | `tools/lcm_expand.py` | Uses **Recursive Common Table Expressions (CTEs)** to drill down to the original raw verbatim messages. |

---

## 5. Technical Philosophies

*   **Verbatim Recall**: Unlike RAG (which returns slices), LCM's `expand` tool returns the *exact* original conversation turn, ensuring logic isn't lost in translation.
*   **Prompt Leakage Sanitization**: `summary_store.py` includes a `_sanitize_content` filter that strips internal XML tags (like `<think>`, `<previous_context>`) before saving to DB, preventing meta-prompts from infecting the history.

---
*Implementation Version: Python-native (Ported from TypeScript/lossless-claw)*