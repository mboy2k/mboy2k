# Cloud Browser 24/7 — trình duyệt headless cho AI agent

Trình duyệt Chromium chạy liên tục 24/7 trong workspace Hoplite, điều khiển qua API HTTP.
Agent trợ lý của bạn chỉ cần gọi REST là mở trang, đọc nội dung, click, gõ, chụp màn hình
— không cần cài browser ở đâu cả.

## Kiến trúc

```
Agent ──HTTP──> Hoplite Preview (HTTPS, token bảo mật của platform)
                     │  FastAPI (auth X-API-Key)
                     └─ Chromium (Playwright) trong sandbox Hoplite
```

## Chạy trên Hoplite (mặc định)

Preview được quản lý bằng script đã cấu hình trong dự án:

- **Setup script** (idempotent): tạo `.venv`, cài `requirements.txt`, tải Chromium.
- **Run script**: nạp `.env` (chứa `BROWSER_API_KEY`), chạy
  `.venv/bin/python cloud_browser/app.py` tại cổng 3000.

Preview nằm trong Preview panel của thread (reference `agent-preview:3000/`).
Sửa code xong chỉ cần start lại preview để nạp bản mới.

## Deploy nơi khác (tùy chọn)

**Modal** — giữ `keep_warm=1` để container luôn sống, không cold start:

```bash
pip install modal
modal token new                                  # đăng nhập Modal

modal secret create browser-api-key BROWSER_API_KEY=dat-khoa-o-day

modal deploy cloud_browser/app.py
```

**VPS thường** — `pip install -r requirements.txt && playwright install --with-deps chromium`,
rồi chạy `python cloud_browser/app.py` dưới systemd/tmux (cổng mặc định 8099, đổi qua `PORT`).

## API

| Endpoint | Body (JSON) | Trả về |
|---|---|---|
| `POST /open` | `{url, session_id?, timeout_ms?}` | `{session_id, url, http_status}` |
| `POST /snapshot` | `{session_id}` | `{title, url, text, links[]}` — nội dung đã render JS |
| `POST /screenshot` | `{session_id, full_page?}` | ảnh PNG |
| `POST /click` | `{session_id, selector}` | `{ok}` |
| `POST /type` | `{session_id, selector, text, submit?}` | `{ok}` |
| `POST /press` | `{session_id, key}` (vd `Enter`) | `{ok}` |
| `POST /eval` | `{session_id, script}` | kết quả JS |
| `POST /wait` | `{session_id, selector}` | `{ok}` |
| `POST /close` | `{session_id}` | `{ok}` |
| `GET /health` | — | trạng thái service |

Mọi endpoint (trừ `/health`) yêu cầu header `X-API-Key`. Tài liệu tương tác tại `/docs`.
Session sống 15 phút giữa các lần dùng (tự dọn), tối đa 8 session song song.

## Ví dụ cho agent

```bash
BASE=<URL preview hoặc http://localhost:3000>
KEY=dat-khoa-o-day

SID=$(curl -s $BASE/open -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com"}' | jq -r .session_id)

curl -s $BASE/snapshot -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\"}" | jq .
```

Hoặc dùng client Python có sẵn:

```python
from cloud_browser.client_example import CloudBrowser

b = CloudBrowser("http://localhost:3000", "dat-khoa-o-day")
b.open("https://example.com")
print(b.snapshot()["text"])
b.screenshot("page.png", full_page=True)
b.close()
```

## Chạy thử cục bộ

```bash
pip install -r requirements.txt && playwright install --with-deps chromium
python cloud_browser/app.py        # chạy tại :8099, đặt BROWSER_API_KEY để bật auth
```

## Ghi chú

- Trên Hoplite, key nằm trong file `.env` (đã gitignore) ở thư mục workspace.
- Trên Modal, chưa tạo secret `browser-api-key`? Service vẫn deploy được nhưng
  **không có auth** — hãy tạo secret sớm.
- Selector là CSS selector của Playwright, hỗ trợ thêm `text=...` nếu cần.
