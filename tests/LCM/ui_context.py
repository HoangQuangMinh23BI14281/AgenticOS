HTML_PART = """
        <div class="column">
            <div class="column-header">CONTEXT WINDOW <span id="stat-budget" style="color:var(--blue)">0/8000</span></div>
            <div class="scroller" id="context-scroller"></div>
        </div>
"""

JS_PART = """
        let ctxHtml = '';
        if (data.active_summaries) {
            data.active_summaries.forEach(s => {
                ctxHtml += `<div class="summary-capsule">
                    <div style="font-weight:800; font-size:10px; margin-bottom:4px; opacity:0.8;">ID: ${s.id} (${s.tokens} tok)</div>
                    <div>${s.content}</div>
                </div>`;
            });
        }
        if (data.all_tail_msgs && data.all_tail_msgs.length > 0) {
            data.all_tail_msgs.forEach(m => {
                const isUser = m.role === 'user';
                ctxHtml += `<div class="bubble ${isUser ? 'bubble-user' : 'bubble-ai'}">
                    <div class="bubble-role">${isUser ? 'User' : 'AI'}</div>
                    <div>${m.content}</div>
                </div>`;
            });
        }
        const scroller = document.getElementById('context-scroller');
        
        // Chỉ cuộn xuống đáy nếu user đang ở gần đáy, tránh giật màn hình khi đọc
        const isScrolledToBottom = scroller.scrollHeight - scroller.clientHeight <= scroller.scrollTop + 50;
        scroller.innerHTML = ctxHtml;
        if(isScrolledToBottom) {
            scroller.scrollTop = scroller.scrollHeight;
        }
"""