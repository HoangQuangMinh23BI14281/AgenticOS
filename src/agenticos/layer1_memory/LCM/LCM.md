# HƯỚNG DẪN CHI TIẾT WORKFLOW CỦA LCM (AgenticOS)

Tài liệu này giải thích chi tiết **Cách thức hoạt động (Workflow)** và **Kiến trúc (Architecture)** thực tế của module **Lossless Context Management (LCM)** được viết bằng Python thuần trong hệ sinh thái AgenticOS. Mọi thứ được thiết kế để giải quyết bài toán: *Làm sao để Agent có trí nhớ vô hạn mà không bao giờ bị tràn RAM, chậm máy hay mất dữ liệu.*

---

## PHẦN 1: HỆ QUẢN TRỊ CƠ SỞ DỮ LIỆU (THE IMMUTABLE STORE)

Khác với các chatbot thông thường lưu lịch sử bằng "mảng" (array) trên RAM, LCM dùng **SQLite (với WAL Mode)** làm bộ nhớ vĩnh cửu.

### 1. Kiến trúc Bảng (Schema)
Dữ liệu được chia làm 3 bảng cốt lõi để đảm bảo không dư thừa:
1.  **`messages` (Dữ liệu thô - Bất biến):** Lưu 100% nội dung gốc của `user` và `assistant`, kèm số đếm `token_count`. Nội dung ở đây **không bao giờ bị xóa hay sửa**.
2.  **`summaries` (Mạng lưới nén DAG):** Lưu các phiên bản đã nén của lịch sử (Summary). Hỗ trợ nén đa tầng (Depth 0, 1, 2...). Nếu hệ thống bị lỗi mạng khi gọi LLM (như bạn vừa gặp), nó sẽ lưu chuỗi cảnh báo `[Truncated - oldest context removed]` vào đây.
3.  **`context_items` (Con trỏ/Sơ đồ):** Bảng này **không chứa nội dung chữ**. Nó chỉ lưu các "con trỏ" ID (`message_id` hoặc `summary_id`) và thứ tự (`ordinal`) của những gì *đang được bật* trong cửa sổ ngữ cảnh hiện tại.

### 2. Thuật toán "Connection Pool"
Để tránh lỗi `[Errno database is locked]` khi Agent vừa chat vừa nén dữ liệu ngầm, LCM thiết kế một `ConnectionPool`:
-   **Write (Ghi):** Bị khóa chốt (Thread Lock), chỉ 1 tiến trình được phép ghi vào DB tại một thời điểm.
-   **Read (Đọc):** Cho phép hàng loạt tiến trình đọc cùng lúc nhờ cơ chế `WAL Mode` của SQLite.

---

## PHẦN 2: ĐỘNG CƠ KIỂM SOÁT NGỮ CẢNH (CONTEXT ENGINE LOOP)

Đây là vòng lặp xương sống chạy mỗi khi người dùng gửi 1 tin nhắn.

### 1. Nạp tin nhắn mới (Ingestion)
Khi chạy `engine.ingest_message()`, tin nhắn lập tức được lưu vào bảng `messages` và bảng `context_items` để Agent có thể nhìn thấy nó.

### 2. Lắp ráp Ngữ cảnh (Assembly) & Bindle
Khi chạy `engine.assemble()`, LCM quét qua bảng `context_items` để trộn lại thành chuỗi Text đưa cho AI. Ở đây có khái niệm cực kỳ quan trọng:
*   **Bindle (Gói mang theo):** Là các Summary/Message đang nằm trong chuỗi `active`. LCM sẽ nạp chúng lên đầu prompt thông qua một thẻ `<lcm_metadata>`, báo cho AI biết "Bạn đang có các gói tin này trong tay".
*   **Archive Stub (Lưu trữ sâu):** Là những Summary quá cũ, đã bị "đẩy" ra khỏi cửa sổ hiện tại (nằm trong DB nhưng không có trong `context_items`).

### 3. Vòng bảo vệ Context (Compaction Thresholding)
Thay vì chờ cửa sổ ngữ cảnh vỡ bục (VD: 32K tokens) mới nén, LCM được thiết lập ngưỡng `context_threshold = 0.55` (Nén từ sớm khi mới đầy 55%). Điều này giúp Agent không bao giờ mắc bệnh "Lost in the Middle" (Quên thông tin ở giữa).

---

## PHẦN 3: LEO THANG 3 CẤP ĐỘ KHI NÉN (3-LEVEL ESCALATION)

Khi tổng Token vượt ngưỡng, Engine kích hoạt hệ thống **Compaction** chạy ngầm. Để đảm bảo không bao giờ Crash hệ thống ngay cả khi LLM hư, mất mạng (như lỗi Ollama Offline), việc nén diễn ra qua 3 bước:

1.  **Level 1 (LLM Summarization):** Gọi LLM (VD: `deepseek-r1:1.5b`) đọc lịch sử cũ và "nhai" lại thành 1 đoạn tóm tắt ngắn nhưng không mất chi tiết quan trọng.
2.  **Level 2 (Strict Bullet Points):** Nếu Level 1 thất bại (LLM trả về câu quá dài hơn cả bản gốc), bắt buộc LLM tóm tắt bằng Gạch đầu dòng siêu ngắn.
3.  **Level 3 (Deterministic Fallback):** Nếu mạng rớt (Timeout) hoặc Ollama tắt định tuyến, code Python sẽ tự ra tay **"Trảm"** (cắt bỏ thô bạo đoạn text đuôi và nhét vào chữ `[Truncated - oldest context removed]`).
    *   *Đây là tính năng Bảo vệ sinh tồn (Safety Mechanism), giữ cho hệ thống chạy tiếp thay vì chết đứng tung lỗi 500.*

---

## PHẦN 4: CÔNG CỤ ĐỌC BỘ NHỚ (MEMORY-ACCESS TOOLS)

Làm sao AI đọc lại được dữ liệu cũ nếu nó đã bị biến thành Bindle hoặc Archive? LCM cấp cho Agent 3 cái Tools (Function Calling):

1.  **`lcm_grep (pattern)`:** 
    Sử dụng công cụ tìm kiếm siêu tốc **FTS5** (Full-text Search engine ảo của SQLite) để tìm lại từ khóa chính xác từ toàn bộ các tin nhắn thô ở quá khứ.
2.  **`lcm_describe (id)`:** 
    Giải thích cho Agent biết Summary X này thực chất đang bọc bao nhiêu tin nhắn thô bên trong, nó là đồ mang theo trên người (Bindle) hay bị cất trong tủ (Archive).
3.  **`lcm_expand (summary_id)`:** 
    Công cụ mạnh nhất — **"Bung nén"**. Nó dùng SQL Đệ quy (Recursive CTEs) để gỡ rối DAG Network, lôi đúng 100% nguyên văn (Verbatim) những gì đã bị nén ra cho Agent đọc.
    *   *Quy luật thép:* Tránh để Agent gốc bung nén một cục dữ liệu 100.000 tokens tự đánh sập context của chính nó. Thường chỉ Agent con (Sub-agent) mới được cấp quyền gọi Tool này.

---

## TỔNG KẾT VÒNG LẶP SỰ SỐNG CỦA MỘT TIN NHẮN

1.  **Sinh ra:** Bạn chat "Xin chào".
2.  **Ghi chép:** LCM lẳng lặng khắc nó vào đá (SQLite bảng `messages` & `context_items`).
3.  **Căng phồng:** Cuộc hội thoại kéo dài 5 tiếng, Token lên mốc báo động (55%).
4.  **Siết chặt:** Hệ thống ngầm gọi Ollama, nhóm 50 tin nhắn cũ lại thành 1 Summary (lưu vào bảng `summaries`), cập nhật lại `context_items`.
5.  **Bất tử:** Chữ của bạn vĩnh viễn không mất đi (Lossless), nó chỉ lùi sâu xuống cây DAG. Khi cần thiết, Agent bấm nút `lcm_expand` là nó lại hiện nguyên hình y như cũ.