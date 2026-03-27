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
                ctxHtml += `<div class="card summary-card">
                    <div style="color:var(--color-d1); font-weight:700; font-size:11px; margin-bottom:8px;">SUMMARY D${s.depth} | ${s.id}</div>
                    <div style="color:var(--text-main);">${s.content}</div>
                </div>`;
            });
        }
        if (data.all_tail_msgs && data.all_tail_msgs.length > 0) {
          ctxHtml += `<div class="card" style="border-style: dashed; border-color: var(--green);">
              <div style="color:var(--green); font-weight:700; font-size:11px; margin-bottom:4px;">FRESH TAIL (Protected)</div>
              <div class="msg-history">${data.all_tail_msgs.join('<br><br>')}</div>
          </div>`;
        }
        const scroller = document.getElementById('context-scroller');
        
        // Chỉ cuộn xuống đáy nếu user đang ở gần đáy, tránh giật màn hình khi đọc
        const isScrolledToBottom = scroller.scrollHeight - scroller.clientHeight <= scroller.scrollTop + 50;
        scroller.innerHTML = ctxHtml;
        if(isScrolledToBottom) {
            scroller.scrollTop = scroller.scrollHeight;
        }
"""