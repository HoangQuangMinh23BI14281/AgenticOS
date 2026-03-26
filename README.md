# Agentic Operating System Framework

**Tóm tắt kiến trúc:** Một hệ điều hành tác tử (Agentic OS) bất đồng bộ, tối ưu hóa triệt để cho giới hạn phần cứng vi mô (Micro-Edge Devices), đặc biệt nhắm tới môi trường bộ nhớ đồ họa cực thấp (tiệm cận mức 6GB VRAM). 

## 1. Triết lý Kiến trúc (Architectural Philosophy)
WarpOS được thiết kế để tối đa hóa năng lực suy luận của các mô hình ngôn ngữ kích thước nhỏ (ví dụ: Qwen 2.5/3.5 - 4B) thông qua phương pháp can thiệp trực tiếp vào chu trình giải mã (Decoding Loop). Hệ thống loại bỏ sự phụ thuộc vào các tầng trừu tượng (Abstraction Layers) phức tạp, thay vào đó áp dụng cơ chế quản lý trạng thái tất định (Deterministic State Management) và thuật toán rẽ nhánh theo hướng tự đánh giá độ không chắc chắn (Epistemic Uncertainty).

---

## 2. Cấu trúc Hệ thống 

### Lớp 1: Nền tảng Ký ức & Động cơ Giải mã (Memory Foundation & Decoding Engine)
*Mô đun can thiệp phần cứng, tối ưu hóa VRAM và kiểm soát phân bổ token ở mức độ nguyên thủy.*

- [ ] **Topological Synapse (Warp-Cortex):** Triển khai kỹ thuật nén đám mây điểm tô pô để thay thế bộ nhớ đệm truyền thống (KV Cache). Cơ chế này cho phép thực thi suy luận đa luồng mà khối lượng VRAM yêu cầu gần như không gia tăng theo hàm mũ.
- [ ] **Native LCM (Lossless Context Management):** Quản lý ngữ cảnh không mất mát thông qua cấu trúc dữ liệu Python thuần túy. Áp dụng kỹ thuật Cắt cụt cứng (Hard Truncation) để trích xuất và lưu trữ biến số toán học, loại bỏ hoàn toàn việc sử dụng LLM để tóm tắt văn bản, từ đó hỗ trợ hệ thống thu hồi 100% dung lượng KV Cache rác sau mỗi tác vụ.
- [ ] **DTS (Decoding Tree Sketching):** Tích hợp thuật toán đo lường độ bất định thông qua kỹ thuật Monkey Patching trực tiếp vào hàm `generate_async`. Hệ thống liên tục tính toán giá trị Entropy ($H$) và Varentropy ($W$) từ phân phối `logprobs`. Cờ rẽ nhánh (Branching Flag) chỉ được kích hoạt khi mô hình sinh ra "Token Quyết định" (điểm có giá trị Varentropy vượt ngưỡng).

### Lớp 2: Lập Kế hoạch & Điều phối Vĩ mô (Macro-Planning & Orchestration)
*Mô đun định tuyến dữ liệu dựa trên các tín hiệu suy luận từ Lớp 1.*

- [ ] **AoT (Atom of Thoughts) Planner:** Bộ phân tích logic. Giải mã cấu trúc văn bản đầu vào và phân rã bài toán phức tạp thành các Trạng thái Nguyên tử (Atomic Tasks) có tính độc lập cao. Khởi tạo một Đồ thị có hướng không chu trình (DAG) để giới hạn cửa sổ ngữ cảnh cần thiết.
- [ ] **DPTS (Dynamic Parallel Tree Search):** Bộ định tuyến bất đồng bộ.
  - [ ] Tiếp nhận tín hiệu độ không chắc chắn từ mô đun DTS.
  - [ ] Triển khai toán tử `llm_map` để phân phối các hạt nguyên tử độc lập vào các luồng xử lý song song.
  - [ ] Quản trị vòng đời của luồng: Kích hoạt *Early Stop* để triệt tiêu các nhánh phi logic (thu hồi VRAM) hoặc *Deep Seek* để nhân bản các nhánh có xác suất hội tụ cao.

### Lớp 3: Môi trường Thực thi Bất đồng bộ (In-Flight Async Environment)
*Không gian thực thi song song thời gian thực dựa trên vi kiến trúc Warp-Cortex.*

- [ ] **The River (Executor Thread):** Luồng khởi tạo tác vụ chính. Tiếp nhận dữ liệu từ Lớp 2, thực hiện tuần tự hóa đầu ra thành mã nguồn Python hoặc SymPy để giải quyết các nút trên đồ thị DAG.
- [ ] **The Stream (Critic Phase 1):** Luồng giám sát ngầm. Quét định kỳ ma trận KV Cache của luồng Executor. Triển khai phương pháp **Referential Injection** (Tiêm mã tham chiếu): Chủ động chèn ma trận sửa lỗi vào luồng suy nghĩ của Executor khi phát hiện sai sót cú pháp hoặc vòng lặp vô hạn, không yêu cầu tái khởi động chu trình sinh token.

### Lớp 4: Hộp cát Thực thi & Vòng lặp Phản tư (Execution Sandbox & Reflexion)
*Mô đun đánh giá lỗi tuyến tính và kiểm chứng trạng thái cô lập.*

- [ ] **RLM REPL (Python Sandbox):** Môi trường đóng gói (Containerized) dùng để biên dịch và thực thi mã nguồn sinh ra từ Lớp 3, kiểm soát chặt chẽ các luồng `stdout` và `stderr`.
- [ ] **Critic Phase 2 (Dynamic Judge):** Thuật toán đánh giá đối chiếu. So sánh kết quả từ REPL với mục tiêu của nút nguyên tử (AoT Node). Phát cờ VETO nếu phát hiện sai lệch về mặt logic toán học.
- [ ] **Reflexion Loop:** Cơ chế tự hiệu chỉnh có giới hạn (`max_retries`). Tiến trình: Tiếp nhận ngoại lệ $\rightarrow$ Tổng hợp tham số lỗi (Reflection) $\rightarrow$ Tái cấu trúc mã nguồn. Nếu số lần thất bại vượt giới hạn, phát tín hiệu cho DPTS để tiến hành loại bỏ nhánh.

### Lớp 5: Màng lọc Cơ chế Nơ-ron (Mechanistic Neural Filters)
*Chấm điểm hậu kỳ (Post-hoc Evaluation) dựa trên đặc tính của cấu trúc mạng nơ-ron nhằm triệt tiêu hiện tượng ảo giác logic.*

- [ ] **INTRYGUE Scanner:** Thuật toán nội soi mô hình. Đánh giá hệ thống Induction Heads để đo lường tỷ lệ SinkRate, thực hiện bù trừ trọng số cho các chuỗi mã nguồn dài nhưng duy trì được tính kế thừa biến số mạch lạc.
- [ ] **The Sequential Edge (IEW):** Đo lường tính hội tụ của chuỗi thông qua giá trị Trọng số Nghịch đảo Entropy ($1/H$), xác định mức độ tự tin tuyệt đối của mô hình đối với nhánh suy luận.
- [ ] **Final Selector:** Bộ chốt chặn cuối cùng. Trích xuất hàm `FINAL_VAR()` từ luồng mã nguồn vượt qua quy trình kiểm thử Lớp 4 và đạt tổng điểm cơ chế Lớp 5 cao nhất để làm kết quả xuất ra.

---

## 3. Nền tảng Công nghệ (Technical Stack)
- **Trình quản lý gói (Package Manager):** `uv`
- **Khung tính toán học sâu (Deep Learning Framework):** `torch` (Yêu cầu cấu hình CUDA 12.1), `transformers`, `accelerate`
- **Xử lý Toán học & Biểu thức (Symbolic Mathematics):** `sympy`
- **Ràng buộc dữ liệu tĩnh (Static Type Validation):** `pydantic`
- **Mục tiêu Mô hình (Target Architecture):** Qwen 2.5/3.5 - 4B (hoặc các mô hình kích thước tương đương hỗ trợ kiến trúc Transformer).