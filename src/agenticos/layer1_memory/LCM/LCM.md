PHẦN 1: HỆ QUẢN TRỊ CƠ SỞ DỮ LIỆU (THE IMMUTABLE STORE)
Bạn không thể dùng các mảng (arrays) đơn giản trên RAM để lưu lịch sử nữa. Bạn cần một cơ sở dữ liệu thực sự có hỗ trợ Transaction (Giao dịch), Foreign Keys (Khóa ngoại) và Full-text Search (Tìm kiếm toàn văn bản). Nhóm tác giả khuyến nghị sử dụng PostgreSQL (hoặc SQLite cho môi trường nhúng)
.
Bạn cần thiết kế 3 Table (Bảng) cốt lõi
:
Bảng Messages (Dữ liệu thô - Bất biến):
id (UUID)
role (user, assistant, tool)
content (Text thô)
token_count (Số lượng token)
timestamp (Thời gian)
summary_id (Khóa ngoại trỏ đến bảng Summaries - Null nếu chưa bị nén)
Index: Tạo index tìm kiếm full-text cho cột content.
Bảng Summaries (Mạng lưới DAG tóm tắt):
id (UUID)
type (Enum: LEAF - tóm tắt tin nhắn gốc, CONDENSED - tóm tắt của các tóm tắt cũ)
content (Đoạn text tóm tắt do LLM sinh ra)
token_count
parent_ids (Mảng UUID trỏ về các message/summary mà nó đại diện - để bảo toàn con trỏ Lossless Pointers).
Bảng Large_Files (Quản lý File ngoài ngữ cảnh):
id (UUID)
file_path (Đường dẫn vật lý trên ổ cứng)
mime_type (Loại file)
exploration_summary (Chuỗi tóm tắt cấu trúc file)

--------------------------------------------------------------------------------
PHẦN 2: ĐỘNG CƠ KIỂM SOÁT NGỮ CẢNH (CONTEXT ENGINE LOOP)
Đây là linh hồn của LCM, chạy ngầm ở mỗi lượt hội thoại (Turn).
1. Khai báo hằng số Threshold:
TAU_SOFT (Ví dụ: 25.000 tokens) - Ngưỡng bắt đầu nén ngầm.
TAU_HARD (Ví dụ: 30.000 tokens) - Ngưỡng ép buộc chặn hệ thống để nén.
FILE_THRESHOLD (Ví dụ: 25.000 tokens) - Ngưỡng dung lượng file tải lên
.
2. Luồng thực thi (Engine Control Loop) bằng Python asyncio:
async def process_turn(new_message, active_context):
    # 1. Lưu ngay vào DB Bất biến
    db.insert_message(new_message)
    active_context.append(new_message)
    
    current_tokens = count_tokens(active_context)
    
    # 2. Xử lý nén
    if current_tokens > TAU_HARD:
        # Chặn luồng (blocking), ép buộc nén ngay lập tức
        active_context = await force_compact(active_context)
    elif current_tokens > TAU_SOFT:
        # Nén chạy ngầm background, user vẫn chat bình thường
        asyncio.create_task(background_compact(active_context))
        
    return active_context
3. Hàm Leo thang 3 Cấp độ (Three-Level Escalation)
: Để tránh việc LLM nén bị lỗi (sinh ra text dài hơn cả text gốc làm tràn RAM), hàm compact() của bạn phải có 3 chốt chặn:
Level 1: Gọi LLM phụ (vd: Haiku) tóm tắt giữ nguyên chi tiết (Detail-preserving). Nếu output < input → Thành công.
Level 2: Nếu Level 1 thất bại, gọi LLM ép tóm tắt dạng gạch đầu dòng (Bullet points) với giới hạn max_tokens = 50% input.
Level 3: Nếu LLM vẫn ngoan cố (ảo giác), dùng Python code cắt cụt chuỗi (Deterministic array truncation) dựa trên thuật toán lấy đuôi. Đảm bảo 100% không bao giờ tràn RAM.

--------------------------------------------------------------------------------
PHẦN 3: BỘ CHUYỂN HƯỚNG FILE LỚN (LARGE FILE DISPATCHER)
Khi User upload 1 file, không được đập thẳng vào Active Context. Code của bạn phải
:
Đếm token file. Nếu < FILE_THRESHOLD, cho vào Context.
Nếu > FILE_THRESHOLD, lưu file ra đĩa và chạy hàm Exploration_Summary():
Nếu là JSON/SQL/CSV: Code Python tự trích xuất Lược đồ (Schema), tên cột.
Nếu là Code (Python/JS): Code Python dùng Regex hoặc AST trích xuất tên Hàm (Function signatures), tên Lớp (Classes).
Nếu là Text thuần: Gọi LLM phụ tóm tắt.
Đưa vào Active Context một chuỗi ID tĩnh: [FILE_ID: 123 | PATH: /data/logs.txt | SUMMARY: {Exploration_Summary}]
.

--------------------------------------------------------------------------------
PHẦN 4: CODE CÁC CÔNG CỤ ĐỌC BỘ NHỚ (MEMORY-ACCESS TOOLS)
Bạn cần cung cấp 3 Tool (dạng JSON Schema function calling) cho LLM
:
lcm_grep(pattern: str, summary_id: Optional[str]):
Backend thực thi: Dùng SQL WHERE content ~ pattern query thẳng vào Database.
Tính năng: Phân trang (Pagination) để không trả về quá nhiều kết quả làm tràn context.
lcm_describe(id: str):
Backend thực thi: Trả về JSON chứa metadata của file hoặc summary (độ dài, loại, thời gian).
lcm_expand(summary_id: str):
Backend thực thi: Dò tìm parent_ids trong DB và lôi toàn bộ lịch sử thô (verbatim) ra.
Luật thép kiến trúc: Root Agent (Tác tử chính) KHÔNG BAO GIỜ được phép gọi tool này để tránh tự làm nổ Context của mình. Chỉ khi Root Agent gọi Sub-agent (Tác tử phụ), Sub-agent đó mới được dùng lcm_expand trong một session riêng biệt, đọc nội dung và trả về kết quả rút gọn cho Root Agent
.

--------------------------------------------------------------------------------
PHẦN 5: CÁC TOÁN TỬ ĐỆ QUY (OPERATOR-LEVEL RECURSION)
Đừng bắt LLM tự viết code vòng lặp for chunk in context. Hãy code sẵn các Tools phân luồng (Map-Reduce) bằng Python và đưa cho LLM gọi
:
llm_map(input_path, prompt, output_schema):
Backend Python: Đọc file input_path (định dạng JSONL). Dùng asyncio.gather với Concurrency=16 (16 worker song song). Gọi LLM API cho từng dòng.
Ép output phải tuân thủ output_schema (dùng thư viện Pydantic/Instructor). Code tự động retry nếu LLM trả về sai JSON.
agentic_map(input_path, prompt, output_schema, read_only):
Backend Python: Giống llm_map nhưng thay vì gọi 1 cú API, nó khởi tạo N cái Sub-Agents độc lập (có công cụ, được phép ghi file).
Task(delegated_scope, kept_work) (Công cụ tạo Tác tử phụ):
Quy luật chống lặp vô tận (Infinite-recursion guard): Hàm backend bắt buộc kiểm tra xem LLM có điền field kept_work (phần việc nó sẽ tự làm) không. Nếu chuỗi này rỗng, Engine Throw Error từ chối tạo Sub-agent, ép tác tử cha phải tự làm. Tránh việc tác tử lười biếng giao hết 100% việc cho tác tử con tạo ra vòng lặp vô tận
.

--------------------------------------------------------------------------------
🛠️ TÓM TẮT TECH STACK CẦN CHUẨN BỊ ĐỂ CODE NGAY:
Core Ngôn ngữ: Python 3.11+ với asyncio (bắt buộc để chạy đa luồng các Sub-agents và LLM-Map).
Cơ sở dữ liệu: PostgreSQL (sử dụng thư viện asyncpg hoặc SQLAlchemy để ghi dữ liệu bất đồng bộ).
Quản lý Pydantic / Schema: Pydantic để kiểm duyệt JSON đầu ra của llm_map và các công cụ.
Đếm Token (Token Counting): Thư viện tiktoken (nếu dùng OpenAI) hoặc tokenizer tương ứng của mô hình bạn dùng để tính toán chính xác biến TAU_SOFT và TAU_HARD.
Mô hình phụ: Tích hợp một API của LLM rẻ, nhanh (như Claude 3.5 Haiku, Gemini 1.5 Flash hoặc Llama-3-8B local) chỉ để chuyên làm nhiệm vụ đè nén (Compaction) chạy ngầm
.
Bạn bắt đầu viết code bằng cách dựng Lớp Database (Immutable Store) trước tiên, vì mọi Tool và Engine loop đều phụ thuộc vào khả năng lưu và truy xuất dữ liệu thô!