# Cloud Browser — hướng dẫn cho agent

Trình duyệt Chromium headless điều khiển qua HTTP. Dùng cho: đọc trang (đã render JS),
click/điền form, đăng nhập giữ được, chụp màn hình, chạy JS.

## Khám phá tính năng
- `GET /` — danh sách actions và features (nguồn chân lý, luôn cập nhật theo code)
- `GET /openapi.json` — schema đầy đủ mọi endpoint; `GET /docs` — UI thử
- MCP client: kết nối `{BASE}/mcp`, thấy ngay các tool `browser_*`

## Xác thực
Header `X-API-Key` trên mọi request (trừ `/` và `/health`). MCP dùng header cùng tên.

## Workflow đọc trang
1. `POST /open` `{"url": "https://..."}` → nhận `session_id`
2. `POST /snapshot` `{"session_id", "format": "markdown"}` → title + text + links
3. `POST /close` `{"session_id"}` khi xong

## Workflow thao tác (login, form)
1. `POST /open` với `"profile": "ten-dang-nhap"` → cookie/localStorage sẽ được lưu
2. `/click`, `/type` (có `submit: true`), `/press`, `/eval` — selector là CSS
3. `POST /close` — state được lưu xuống đĩa; lần sau mở cùng profile là còn đăng nhập
4. Xem phiên đang có: `GET /profiles`; xóa state: `POST /profiles/delete`

## Tùy biến khi `/open` (body JSON)
- `proxy`: `"http://host:port"` / `"http://user:pass@host:port"` / `"socks5://..."`
  (mỗi proxy chạy một process Chromium riêng — nhiều IP song song được)
- `user_agent`, `viewport_width/height`, `is_mobile`, `has_touch`
- `locale`, `timezone`, `geolocation`, `permissions`, `color_scheme`
- `block_resources`: `["images","media","font","stylesheet"]` — nhanh hơn, ít RAM hơn
- `profile`: tên đăng nhập giữ trạng thái (xem workflow ở trên)
- `timeout_ms`, `wait_until` cho navigation

## Lưu ý
- Session sống 15 phút giữa các lần dùng; tối đa 8 session song song
- Request lỗi trả JSON `{"detail": "..."}` với HTTP code rõ (400/401/404/504)
- Screenshot trả PNG binary, `full_page: true` để chụp cả trang dài
