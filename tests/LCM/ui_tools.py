HTML_PART = """
        <div class="column">
            <div class="column-header">TOOLS & CHAT</div>
            <div class="tools-container">
                
                <div class="tool-section" id="t-grep">
                    <div class="tool-head" onclick="toggleTool('t-grep')">LCM_GREP (FTS Search)</div>
                    <div class="tool-body">
                        <input type="text" id="grep-in" class="search-input" placeholder="Search across all DAG depths..." onkeypress="if(event.key==='Enter') doGrep()">
                        <div id="grep-res" class="res-box"></div>
                    </div>
                </div>

                <div class="tool-section" id="t-desc">
                    <div class="tool-head" onclick="toggleTool('t-desc')">LCM_DESCRIBE (Node Manifest)</div>
                    <div class="tool-body">
                        <input type="text" id="desc-in" class="search-input" placeholder="Enter Node ID (e.g. sum_123)..." onkeypress="if(event.key==='Enter') doDesc()">
                        <div id="desc-res" class="res-box"></div>
                    </div>
                </div>

                <div class="tool-section open" id="t-exp">
                    <div class="tool-head" onclick="toggleTool('t-exp')">LCM_EXPAND_QUERY (Fidelity Recall)</div>
                    <div class="tool-body">
                        <div style="display: flex; gap: 8px; margin-bottom: 8px;">
                            <input type="text" id="exp-id" class="search-input" style="flex: 1;" placeholder="Node ID...">
                            <input type="text" id="exp-q" class="search-input" style="flex: 2;" placeholder="What to extract?" onkeypress="if(event.key==='Enter') doExpand()">
                        </div>
                        <button onclick="doExpand()" style="width: 100%; padding: 8px; background: rgba(88, 166, 255, 0.1); border: 1px solid var(--blue); border-radius: 4px; color: var(--blue); font-family: 'JetBrains Mono'; font-size: 11px; font-weight: bold; cursor: pointer; transition: 0.2s;">
                            DISPATCH SUB-AGENT
                        </button>
                        <div id="exp-res" class="res-box"></div>
                    </div>
                </div>

            </div>

            <div class="chat-container">
                <div class="chat-input-wrapper">
                    <input type="text" id="chat-in" class="chat-input" placeholder="Ask AgenticOS memory something..." onkeypress="if(event.key==='Enter') sendChat()">
                    <button class="send-btn" onclick="sendChat()">↑</button>
                </div>
                <div id="chat-status" style="font-size:10px; color:var(--text-muted); text-align:center;">READY</div>
            </div>
        </div>
"""

JS_PART = """
    // Chat Logic
    async function sendChat() {
        const inp = document.getElementById('chat-in');
        const q = inp.value.trim();
        if (!q) return;
        inp.value = '';
        document.getElementById('chat-status').innerText = "AGENT THINKING...";
        await fetch('/chat', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({query: q})
        });
        document.getElementById('chat-status').innerText = "READY";
    }

    // Tools Accordion
    function toggleTool(id) {
        document.querySelectorAll('.tool-section').forEach(el => {
            if(el.id === id) el.classList.toggle('open');
            else el.classList.remove('open');
        });
    }

    // Tool Actions
    async function doGrep() {
        const q = document.getElementById('grep-in').value.trim();
        if (!q) return;
        document.getElementById('grep-res').innerText = "Searching DAG...";
        const res = await fetch(`/grep?q=${encodeURIComponent(q)}`);
        const data = await res.json();
        document.getElementById('grep-res').innerText = data.result || JSON.stringify(data, null, 2);
    }

    async function doDesc() {
        const id = document.getElementById('desc-in').value.trim();
        if (!id) return;
        document.getElementById('desc-res').innerText = "Fetching node manifest...";
        const res = await fetch(`/describe?id=${encodeURIComponent(id)}`);
        const data = await res.json();
        document.getElementById('desc-res').innerText = data.result || JSON.stringify(data, null, 2);
    }

    async function doExpand() {
        const id = document.getElementById('exp-id').value.trim();
        const q = document.getElementById('exp-q').value.trim();
        
        if (!id) {
            document.getElementById('exp-res').innerText = "Error: Missing Node ID.";
            return;
        }
        if (!q) {
            document.getElementById('exp-res').innerText = "Error: Fidelity Recall requires a specific query.";
            return;
        }
        
        document.getElementById('exp-res').innerText = `Delegating task to Sub-Agent on node ${id}...`;
        
        // Truyền cả id và q lên API
        const res = await fetch(`/expand?id=${encodeURIComponent(id)}&q=${encodeURIComponent(q)}`);
        const data = await res.json();
        document.getElementById('exp-res').innerText = data.result || JSON.stringify(data, null, 2);
    }
"""