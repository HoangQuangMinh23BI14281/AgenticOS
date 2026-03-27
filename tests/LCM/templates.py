from .ui_styles import CSS_STYLES
from .ui_context import HTML_PART as CTX_HTML, JS_PART as CTX_JS
from .ui_tree import HTML_PART as TREE_HTML, JS_LOGIC as TREE_JS_LOGIC, JS_RENDER as TREE_JS_RENDER
from .ui_tools import HTML_PART as TOOL_HTML, JS_PART as TOOL_JS

HTML_PAGE = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AgenticOS LCM | Command Center</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
    <style>{CSS_STYLES}</style>
</head>
<body>
    <header>
        <div class="logo">AGENTICOS LCM COMMAND CENTER</div>
        <div class="stats-ribbon">
            <span>STORED: <b id="stat-stored">0</b></span>
            <span>OLLAMA: <b id="stat-ollama">...</b></span>
        </div>
    </header>
    <main>
        {CTX_HTML}
        {TREE_HTML}
        {TOOL_HTML}
    </main>
    
    <script>
        // --- 1. GLOBAL LOGIC (Định nghĩa hàm, xử lý UI tĩnh) ---
        {TOOL_JS}
        {TREE_JS_LOGIC}

        window.addEventListener('resize', () => {{
            if (typeof drawCurves === 'function') drawCurves();
        }});

        // --- 2. SSE STREAM HANDLING (Cập nhật dữ liệu liên tục) ---
        const es = new EventSource("/stream");
        es.onmessage = e => {{
            const data = JSON.parse(e.data);
            
            // Global HUD Stats
            document.getElementById('stat-stored').innerText = data.total_db_messages;
            document.getElementById('stat-ollama').innerText = data.ollama_ok ? "ONLINE" : "OFFLINE";
            document.getElementById('stat-ollama').style.color = data.ollama_ok ? "var(--green)" : "var(--red)";
            document.getElementById('stat-budget').innerText = `${{data.active_tokens.toLocaleString()}} / ${{data.budget.toLocaleString()}}`;

            // Modular Rendering
            {CTX_JS}
            {TREE_JS_RENDER}
        }};
    </script>
</body>
</html>
"""