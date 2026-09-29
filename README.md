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

### Chạy 24/7 trên VPS với systemd (khuyên dùng khi agent cùng máy)

```bash
sudo git clone https://github.com/mboy2k/mboy2k /opt/cloud-browser
cd /opt/cloud-browser
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
sudo .venv/bin/playwright install --with-deps chromium

# sinh key mới riêng cho VPS
printf 'BROWSER_API_KEY=%s\nHOST=127.0.0.1\nPORT=8099\n' "$(openssl rand -hex 24)" > .env

sudo cp deploy/cloud-browser.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cloud-browser
curl -s localhost:8099/health        # kiểm tra
```

`HOST=127.0.0.1` giữ service chỉ nghe trong máy — an toàn nhất khi Hermes chạy cùng VPS.
Xem log: `journalctl -u cloud-browser -f`.

## API

| Endpoint | Body (JSON) | Trả về |
|---|---|---|
| `POST /open` | `{url, session_id?, timeout_ms?, ...tùy chọn}` | `{session_id, url, http_status}` |
| `POST /snapshot` | `{session_id, format?}` (`text`\|`markdown`) | `{title, url, text, links[]}` |
| `POST /screenshot` | `{session_id, full_page?}` | ảnh PNG |
| `POST /click` | `{session_id, selector}` | `{ok}` |
| `POST /type` | `{session_id, selector, text, submit?}` | `{ok}` |
| `POST /press` | `{session_id, key}` (vd `Enter`) | `{ok}` |
| `POST /eval` | `{session_id, script}` | kết quả JS |
| `POST /wait` | `{session_id, selector}` | `{ok}` |
| `POST /close` | `{session_id}` | `{ok}` |
| `GET/POST /cookies` | POST: `{session_id, cookies[]}` | đọc/ghi cookie của phiên |
| `GET /health` | — | trạng thái service |

### Tùy biến theo phiên (đặt trong body của `/open`)

| Trường | Ý nghĩa |
|---|---|
| `proxy` | Proxy cho riêng phiên này: `http://host:port`, `http://user:pass@host:port` hoặc `socks5://...`. Mỗi proxy khác nhau chạy một process Chromium riêng |
| `user_agent` | Tự đặt UA (giả máy thật, mobile, bot Google...) |
| `viewport_width` / `viewport_height` | Kích thước cửa sổ |
| `locale`, `timezone` | Ngôn ngữ và múi giờ (VD `vi-VN`, `Asia/Ho_Chi_Minh`) |
| `geolocation` | `{"latitude": 10.8, "longitude": 106.7}` |
| `permissions` | `["geolocation", "notifications"]` |
| `color_scheme` | `light` / `dark` |
| `is_mobile`, `has_touch`, `device_scale_factor` | Giả lập điện thoại |
| `block_resources` | `["images","media","font","stylesheet"]` — chặn tải tài nguyên nặng, tiết kiệm băng thông/RAM |

Tùy chọn áp lúc tạo phiên; gửi lại kèm `session_id` với tùy chọn mới → phiên được tạo lại.

### Lưu đăng nhập qua các lần đóng/mở (`profile`)

Thêm `"profile": "ten-bat-ky"` vào body của `/open`: cookie và localStorage của phiên
được lưu xuống đĩa lúc đóng, và nạp lại lần sau mở cùng tên profile — **đăng nhập một
lần, giữ mãi** (kể cả qua lần restart service). Xem danh sách: `GET /profiles`;
xóa sạch trạng thái: `POST /profiles/delete {"profile": "..."}`.

Ví dụ: đăng nhập giữ được giữa các phiên:

```bash
curl -s $BASE/open -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"url":"https://shopee.vn","profile":"shop-cua-toi"}'
# ... click, điền form, login ... khi agent xong:
curl -s $BASE/close -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"session_id":"<sid>"}'
# Lần sau mở lại profile "shop-cua-toi" là còn đăng nhập.
```

Mỗi profile hoạt động với mọi proxy — có thể giữ `profile: "tiktok"` qua IP Mỹ,
`profile: "shopee"` qua IP Việt Nam song song.

Ví dụ: mở trang qua proxy, giả máy iPhone, chặn ảnh:

```bash
curl -s $BASE/open -H "X-API-Key: $KEY" -H 'Content-Type: application/json' -d '{
  "url": "https://example.com",
  "session_id": "iphone1",
  "proxy": "socks5://user:pass@proxy-host:1080",
  "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
  "viewport_width": 390, "viewport_height": 844, "is_mobile": true, "has_touch": true,
  "locale": "vi-VN", "timezone": "Asia/Ho_Chi_Minh",
  "block_resources": ["images", "media", "font"]
}'
```

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

## Cho agent khác dùng kiểu gì?

### Cách agent tự khám phá tính năng

Không cần chép tay tài liệu vào prompt — agent tự tra được:

1. `GET /` — trả danh sách actions + mô tả ngắn từng tính năng (nguồn chân lý theo code)
2. `GET /openapi.json` — schema máy đọc đầy đủ mọi endpoint (agent HTTP hiểu bản chất)
3. `GET {BASE}/mcp` — agent MCP thấy ngay 11 tool `browser_*` kèm mô tả
4. **AGENTS.md** ở thư mục gốc repo — bản hướng dẫn dành riêng cho agent (Hermes và
   hầu hết agent đọc tự động file này khi vào repo); cũng có thể dán nội dung file vào
   system prompt nếu agent không có cơ chế đọc file

**1. REST/OpenAPI** — mọi agent có tool HTTP (curl, fetch, code interpreter) dùng được ngay.
Spec OpenAPI machine-readable tại `/openapi.json`, UI thử tại `/docs`.

**2. MCP (Model Context Protocol)** — agent hỗ trợ MCP (Claude Desktop, Claude Code,
Cursor, v.v.) cắm trực tiếp vào `/mcp` với các tool: `browser_open`, `browser_snapshot`,
`browser_screenshot`, `browser_click`, `browser_type`, `browser_eval`, `browser_close`:

```json
{
  "mcpServers": {
    "cloud-browser": {
      "url": "<URL preview>/mcp",
      "headers": { "X-API-Key": "<key>" }
    }
  }
}
```

**3. Python client** — `cloud_browser/client_example.py`, import vào codebase của agent.

Cả ba điệu điều khiển cùng một pool phiên: session tạo bằng REST dùng được bằng MCP và ngược lại.

## Chạy thử cục bộ

```bash
pip install -r requirements.txt && playwright install --with-deps chromium
python cloud_browser/app.py        # chạy tại :8099, đặt BROWSER_API_KEY để bật auth
```

## Ghi chú

- Chi phí trên Modal (gói Starter có ~$30 credit/tháng): giữ container 24/7
  (`MODAL_KEEP_WARM=1`, mặc định 2 CPU/2GB) ước khoảng $40-45/tháng theo giá công bố —
  vượt credit free. Chạy `MODAL_KEEP_WARM=0 MODAL_CPU=1 MODAL_MEM_MIB=1024 modal deploy ...`
  để scale-to-zero: gần như miễn phí, đổi lại sau ~2 phút nghỉ request đầu chờ ~15s.
- VPS 1GB RAM không chạy nổi Chromium — kiến trúc đúng là browser chạy trên Modal,
  agent trên VPS chỉ gọi HTTP.
- Trên Hoplite, key nằm trong file `.env` (đã gitignore) ở thư mục workspace.
- Trên Modal, chưa tạo secret `browser-api-key`? Service vẫn deploy được nhưng
  **không có auth** — hãy tạo secret sớm.
- Selector là CSS selector của Playwright, hỗ trợ thêm `text=...` nếu cần.
