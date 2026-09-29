# Cloud Browser 24/7 — trình duyệt headless cho AI agent

Trình duyệt Chromium chạy trên nền tảng [Modal](https://modal.com), luôn online 24/7,
điều khiển qua API HTTP. Agent trợ lý của bạn chỉ cần gọi REST là mở trang, đọc nội dung,
click, gõ, chụp màn hình — không cần cài browser ở đâu cả.

## Kiến trúc

```
Agent ──HTTP──> https://cloud-browser-<workspace>.modal.run
                     │  FastAPI (auth X-API-Key)
                     └─ Chromium (Playwright) trong container Modal
                        keep_warm=1 → container luôn sống, không cold start
```

## Triển khai (2 bước)

```bash
pip install modal
modal token new                                  # đăng nhập Modal

# 1. Tạo API key (bảo mật; dùng khóa bất kỳ bạn muốn)
modal secret create browser-api-key BROWSER_API_KEY=dat-khoa-o-day

# 2. Deploy — URL trả về chính là trình duyệt đám mây của bạn
modal deploy cloud_browser/app.py
```

Sau này cập nhật code chỉ cần chạy lại `modal deploy`. Tắt hẳn: `modal app stop cloud-browser`.

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
BASE=https://cloud-browser-xxx.modal.run
KEY=dat-khoa-o-day

SID=$(curl -s $BASE/open -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com"}' | jq -r .session_id)

curl -s $BASE/snapshot -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d "{\"session_id\":\"$SID\"}" | jq .
```

Hoặc dùng client Python có sẵn:

```python
from cloud_browser.client_example import CloudBrowser

b = CloudBrowser("https://cloud-browser-xxx.modal.run", "dat-khoa-o-day")
b.open("https://example.com")
print(b.snapshot()["text"])
b.screenshot("page.png", full_page=True)
b.close()
```

## Chạy thử cục bộ (không cần Modal)

```bash
pip install -r requirements.txt && playwright install --with-deps chromium
python cloud_browser/app.py        # chạy tại :8099, đặt BROWSER_API_KEY để bật auth
```

## Ghi chú

- Chi phí: keep_warm giữ 1 container (2 CPU / 2GB) chạy liên tục — xem dashboard Modal
  để theo dõi. Muốn tiết kiệm, bỏ `keep_warm=1` trong `app.py`: service vẫn online 24/7
  nhưng request đầu sau ~2 phút nghỉ sẽ chờ cold start.
- Chưa tạo secret `browser-api-key`? Service vẫn deploy được nhưng **không có auth** —
  hãy tạo secret sớm.
- Selector là CSS selector của Playwright, hỗ trợ thêm `text=...` nếu cần.
