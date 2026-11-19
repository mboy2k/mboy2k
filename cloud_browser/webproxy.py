"""Browser-facing view of the cloud browser so a person can browse through the sandbox.

`/` answers browsers with a small form, `GET /view?url=...` renders the target page
with the cloud browser and returns title, screenshot, text and clickable links.
Both are reached through the Hoplite Preview link, which carries its own access token,
so they intentionally do not ask for `X-API-Key`; every JSON endpoint still does.
"""

import base64
from html import escape

STYLE = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; margin: 0; }
header { position: sticky; top: 0; z-index: 2; background: Canvas; border-bottom: 1px solid rgba(128,128,128,.35); padding: 10px 14px; }
form { display: flex; gap: 8px; }
input[type=url] { flex: 1; min-width: 0; padding: 9px 10px; font: inherit; }
button { padding: 9px 16px; font: inherit; cursor: pointer; }
main { padding: 16px 14px 40px; max-width: 1100px; }
h1 { font-size: 19px; margin: 4px 0 10px; }
img { max-width: 100%; border: 1px solid rgba(128,128,128,.35); border-radius: 8px; }
pre { white-space: pre-wrap; word-break: break-word; background: rgba(128,128,128,.12); padding: 12px; border-radius: 8px; }
.meta { color: GrayText; font-size: 13px; margin: 6px 0 12px; word-break: break-all; }
.links a { display: block; padding: 3px 0; }
.error { color: #d33; }
.hint { color: GrayText; font-size: 13px; }
.row { display: flex; gap: 16px; flex-wrap: wrap; }
.row > * { flex: 1 1 320px; }
"""

FORM = """
<form action="/view" method="get">
  <input type="url" name="url" value="__URL__" placeholder="https://example.com" required>
  <button>Mở qua sandbox</button>
</form>
"""


def _page(title: str, url: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>{STYLE}</style>
</head>
<body>
<header>{FORM.replace("__URL__", escape(url, quote=True))}</header>
<main>{body}</main>
</body>
</html>
"""


def render_home() -> str:
    body = (
        "<h1>Cloud Browser — xem web qua sandbox</h1>"
        "<p>Nhập địa chỉ rồi bấm <b>Mở qua sandbox</b>: trang được tải bằng Chromium "
        "trong sandbox, trả về ảnh chụp, nội dung chữ và các link bấm được.</p>"
        "<p class=\"hint\">Máy nào cũng dùng được, không cần cài gì: chỉ mở link Preview này. "
        "API JSON (<code>/open</code>, <code>/snapshot</code>, …) và MCP vẫn cần "
        "<code>X-API-Key</code> như cũ.</p>"
    )
    return _page("Cloud Browser", "", body)


def render_error(url: str, message: str) -> str:
    return _page("Lỗi", url, f'<h1 class="error">Không mở được trang</h1>'
                             f"<p class=\"error\">{escape(message)}</p>")


def render_view(url: str, snapshot: dict, png: bytes, session_id: str) -> str:
    title = snapshot.get("title") or url
    text = snapshot.get("text") or ""
    links = snapshot.get("links") or []
    image = base64.b64encode(png).decode()
    rows = "".join(
        f'<a href="/view?url={escape(link.get("href", ""), quote=True)}'
        f'&amp;sid={escape(session_id, quote=True)}">{escape(link.get("text") or link.get("href") or "")}</a>'
        for link in links
        if link.get("href", "").startswith(("http://", "https://"))
    )
    body = (
        f"<h1>{escape(title)}</h1>"
        f'<p class="meta">{escape(snapshot.get("url") or url)}</p>'
        f'<img alt="ảnh chụp trang" src="data:image/png;base64,{image}">'
        f"<h1>Link trên trang</h1><div class=\"links\">{rows or '<p class=\"hint\">Không có link.</p>'}</div>"
        f"<h1>Nội dung chữ</h1><pre>{escape(text)}</pre>"
    )
    return _page(title, url, body)
