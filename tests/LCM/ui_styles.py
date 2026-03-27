CSS_STYLES = """
:root {
    --bg-body: #010409; --bg-panel: #0d1117; --border-panel: #30363d;
    --text-main: #e6edf3; --text-muted: #8b949e;
    --color-d2: #ffcecb; --color-d1: #ff7b72; --color-d0: #d29922; --color-raw: #58a6ff;
    --bg-d1: rgba(255, 123, 114, 0.08); --bg-d2: rgba(255, 206, 203, 0.05);
    --blue: #58a6ff; --orange: #d29922; --green: #3fb950; --red: #f85149;
    --line-solid: #ff7b72; --line-dashed: #484f58;
}
body {
    background-color: var(--bg-body); color: var(--text-main); font-family: 'Outfit', sans-serif;
    margin: 0; padding: 0; height: 100vh; display: flex; flex-direction: column; overflow: hidden;
}
header {
    padding: 12px 24px; background: #0d1117; border-bottom: 1px solid var(--border-panel);
    display: flex; justify-content: space-between; align-items: center; z-index: 10;
}
.logo { font-weight: 700; letter-spacing: 2px; font-size: 14px; color: var(--blue); }
.stats-ribbon { display: flex; gap: 24px; font-size: 11px; font-family: 'JetBrains Mono'; color: var(--text-muted); }
main {
    display: grid; grid-template-columns: 1fr 1.2fr 1fr; gap: 1px; flex: 1; 
    background: var(--border-panel); min-height: 0;
}
.column { background: var(--bg-body); display: flex; flex-direction: column; overflow: hidden; position: relative; }
.column-header {
    padding: 12px 18px; background: rgba(255,255,255,0.02); border-bottom: 1px solid var(--border-panel);
    font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px;
    display: flex; justify-content: space-between; align-items: center; z-index: 10;
}

/* CONTEXT WINDOW: Tăng khoảng thở, dễ đọc text dài */
.scroller { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 16px; }
.card { background: var(--bg-panel); border: 1px solid var(--border-panel); border-radius: 8px; padding: 16px; font-size: 13px; line-height: 1.6; }
.summary-card { border-left: 4px solid var(--color-d1); }
.msg-history { font-size: 12px; color: var(--text-muted); margin-top: 12px; border-top: 1px dashed #30363d; padding-top: 12px; white-space: pre-wrap; }

/* DAG TREE: Trả lại nét đứt cam cho D0, giãn cách các Node rộng ra */
#dag-viewport { flex: 1; overflow: hidden; position: relative; background: #010409; cursor: grab; }
#dag-viewport:active { cursor: grabbing; }
#dag-container { position: absolute; transform-origin: 0 0; min-width: 2000px; min-height: 1000px; }
#dag-svg { position: absolute; top: 0; left: 0; width: 100%; height: 100%; pointer-events: none; z-index: 0; }
.tree { display: flex; justify-content: center; position: absolute; z-index: 1; padding: 60px; width: 100%; }
.tree ul { display: flex; justify-content: center; align-items: flex-start; padding: 0; margin: 60px 0 0 0; gap: 24px; }
.tree li { list-style: none; display: flex; flex-direction: column; align-items: center; }
.node { 
    display: inline-block; padding: 14px 16px; border-radius: 8px; text-align: left; font-size: 11px; line-height: 1.5; 
    min-width: 160px; background-color: var(--bg-panel); box-shadow: 0 8px 24px rgba(0,0,0,0.4); font-family: 'JetBrains Mono', monospace; z-index: 2;
}
.node-d2 { border: 1px solid var(--color-d2); background: var(--bg-d2); }
.node-d1 { border: 1px solid var(--color-d1); background: var(--bg-d1); }
.node-d0 { border: 1px dashed var(--color-d0); } /* Fix chuẩn ảnh thiết kế */
.node-raw { border: 1px dashed var(--color-raw); background: transparent; min-width: 120px; }
.tag { font-weight: 700; margin-bottom: 6px; text-transform: uppercase; font-size: 10px; }
.tag-d2 { color: var(--color-d2); }
.tag-d1 { color: var(--color-d1); }
.tag-d0 { color: var(--color-d0); }
.tag-raw { color: var(--color-raw); }
.meta { color: var(--text-muted); font-size: 10px; }

/* TOOLS & CHAT: Fix tràn viền (Word-wrap) */
.tools-container { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 16px; }
.tool-section { border: 1px solid var(--border-panel); border-radius: 8px; overflow: hidden; background: var(--bg-panel); }
.tool-head { padding: 12px 16px; background: rgba(255,255,255,0.02); cursor: pointer; font-size: 11px; font-weight: 700; color: var(--orange); }
.tool-body { padding: 16px; display: none; background: #010409; border-top: 1px solid var(--border-panel); }
.tool-section.open .tool-body { display: block; }
.search-input { width: 100%; background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 10px 12px; color: #fff; font-size: 12px; outline: none; box-sizing: border-box; }
.search-input:focus { border-color: var(--blue); }
.res-box { font-size: 11px; margin-top: 12px; color: var(--text-muted); max-height: 300px; overflow-y: auto; white-space: pre-wrap; word-wrap: break-word; font-family: 'JetBrains Mono', monospace; line-height: 1.5; }

.chat-container { padding: 20px; border-top: 1px solid var(--border-panel); background: #0d1117; display: flex; flex-direction: column; gap: 12px; z-index: 10; }
.chat-input-wrapper { background: #010409; border: 1px solid var(--border-panel); border-radius: 24px; padding: 8px 8px 8px 16px; display: flex; align-items: center; gap: 12px; }
.chat-input { flex: 1; background: transparent; border: none; color: #fff; outline: none; font-size: 13px; font-family: 'Outfit'; }
.send-btn { background: var(--blue); color: #000; border: none; width: 32px; height: 32px; border-radius: 50%; cursor: pointer; font-weight: 700; display: flex; align-items: center; justify-content: center; transition: 0.2s; }
.send-btn:hover { transform: scale(1.05); }

/* SCROLLBAR THẨM MỸ */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #30363d; border-radius: 10px; }
::-webkit-scrollbar-thumb:hover { background: #484f58; }
"""