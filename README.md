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

Preview được quản lý bằng hai script nằm trong repo (project settings trỏ vào chúng):

- **`scripts/hoplite_setup.sh`** (idempotent): tạo `.venv`, cài `requirements.txt`, tải Chromium,
  và sinh `.env` với `BROWSER_API_KEY` mới nếu file chưa có.
- **`scripts/hoplite_run.sh`**: nạp `.env`, ghi port manifest, chạy
  `.venv/bin/python cloud_browser/app.py` tại cổng 3000 và tự khởi động lại nếu app thoát.

Preview nằm trong Preview panel của thread (reference `agent-preview:3000/`).
Sửa code xong chỉ cần start lại preview để nạp bản mới.

## Máy tính Hoplite (Hoplite PC)

Mở Preview là vào thẳng **Hoplite PC** — một "máy tính" chạy trong workspace, dùng được trên điện thoại:

| App | Việc nó làm |
|---|---|
| 🧮 **Máy tính** | Máy tính bỏ túi: cộng/trừ/nhân/chia, `%`, `±`, hỗ trợ cả bàn phím máy tính |
| ⌨️ **Terminal** | Gõ lệnh thẳng vào sandbox; shell giữ nguyên `cd` và biến môi trường giữa các lệnh |
| 🔐 **Kết nối từ xa** | Trạng thái Tailscale + SSH: mở link đăng nhập, xem lệnh và mật khẩu để vào máy từ thiết bị khác |
| 🌐 **Trình duyệt** | Lướt web bằng Chromium của sandbox: nhập địa chỉ hoặc từ khoá, chạm để click, cuộn, gõ chữ |

Trang chủ hiển thị tên máy, kernel, RAM, thời gian chạy và **IP công khai kèm thành phố/quốc gia của máy**.
Đồng hồ ở thanh dưới hiển thị giờ Việt Nam.

UI gọi backend ở `/pc/*`; app Trình duyệt dùng chung pool session với REST/MCP, nên session mở từ UI
vẫn điều khiển được bằng API và ngược lại. `/` và `/pc/*` không đòi `X-API-Key` vì đã nằm sau Preview
của Hoplite — **chỉ mở từ Preview panel**, đừng dán link preview cho người khác vì Terminal chạy được
lệnh trong máy.

## Truy cập từ xa như máy thật (SSH qua Tailscale)

`scripts/machine_access.sh` dựng "cửa vào" cho máy; nó idempotent và được gọi từ **cả** setup script
(mỗi lần sandbox được cấp lại) **lẫn** run script kèm watchdog 20 giây, nên sshd/tailscaled tự sống lại.

- **sshd** ở cổng 22, đăng nhập `ssh root@<tên máy>` — sandbox bật `no_new_privs` nên `sudo` không
  dùng được cho user thường; vào thẳng root là cách duy nhất có toàn quyền.
- **Tailscale chạy chế độ userspace** (không cần TUN): kết nối TCP vào port N của node được chuyển về
  `localhost:N`, nên SSH chỉ mở trong mạng riêng của chủ máy, không phơi ra internet.
- Mật khẩu SSH sinh mỗi lần sandbox dựng lại, lưu ở `/var/lib/hoplite-pc/ssh-password` và hiện trong
  app **🔐 Kết nối từ xa**.
- `TS_AUTHKEY` (auth key reusable) ⇒ máy **tự vào mạng riêng** sau mỗi lần dựng lại. Không có key thì
  app Kết nối từ xa hiện link đăng nhập, bấm một lần là xong.
- Thêm chìa khoá riêng để khỏi dùng mật khẩu: `BOSS_SSH_PUBKEY="ssh-ed25519 …"` hoặc ghi vào
  `/var/lib/hoplite-pc/authorized_keys`.
- Tuỳ biến: `PC_HOSTNAME` (mặc định `hoplite-pc`), `PC_SSH_USER` (mặc định `root`).

Thiết bị của chủ máy cài Tailscale, đăng nhập cùng tài khoản, rồi:

```bash
ssh root@hoplite-pc        # MagicDNS nếu bật
ssh root@100.x.y.z         # hoặc IP nội bộ hiện trong app Kết nối từ xa
```

**Giới hạn thật:** sandbox thuộc thread và bị thu hồi khi thread nghỉ — không có bộ hẹn giờ nào ở đây
để "đánh thức" nó. Máy tự dựng lại đầy đủ khi sandbox được cấp lại (setup script chạy lại), và trong
lúc máy sống thì watchdog giữ sshd/tailscaled/app luôn chạy. Muốn máy chạy liên tục thật sự thì thread
phải được hoạt động đều (mở Preview/gửi tin) hoặc máy phải chạy trên hạ tầng riêng (VPS/Modal ở mục dưới).

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

Mọi endpoint (trừ `/`, `/info`, `/health` và nhóm `/pc/*` của Hoplite PC) yêu cầu header `X-API-Key`.
Thông tin service dạng JSON nằm ở `/info`; tài liệu tương tác tại `/docs`.
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
python cloud_browser/app.py        # cổng theo PORT trong .env (mặc định 8099)
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
