# Proxy cho BP Proxy Switcher (gost + tunnel bore.pub)

Proxy **HTTP + SOCKS5** có user/pass, chạy trong sandbox Hoplite và mở ra internet
qua `bore.pub`. Cắm thẳng vào BP Proxy Switcher, không cần cài gì trên máy.

## Endpoint hiện tại

| Mục | Giá trị |
|-----|---------|
| Host | `bore.pub` |
| Port | `37635` |
| User | `boss` |
| Pass | xem `auth.txt` (cùng thư mục) |
| Loại | HTTP proxy **hoặc** SOCKS5 — cùng một cổng, gost tự nhận diện |

Kiểm tra nhanh bằng terminal:

```bash
curl -x "http://boss:PASS@bore.pub:37635" https://api.ipify.org
```

## Nhập vào BP Proxy Switcher

1. Mở extension → **Options** → **Add new proxy**.
2. Type = **HTTP** (hoặc SOCKS5), Host = `bore.pub`, Port = `37635`,
   Username = `boss`, Password = trong `auth.txt`.
3. **Test / Save**, rồi chọn proxy này khi muốn dùng.

## Vận hành trong sandbox

| Việc | Lệnh |
|------|------|
| Bật / kiểm tra | `bash proxy/start.sh` (tự tải lại binary nếu thiếu, in endpoint) |
| Tắt | `bash proxy/stop.sh` |
| Đổi mật khẩu | sửa `auth.txt` → `bash proxy/stop.sh` → `bash proxy/start.sh` |

Watchdog chạy 30 giây/lần, tự dựng lại `gost`/`bore` nếu chúng chết.
Máy dựng lại cũng không cần làm gì: `scripts/hoplite_setup.sh` (chạy tự động khi
workspace được dựng) sinh mật khẩu lần đầu, tải binary và bật sẵn watchdog.

## Giới hạn (đọc một lần)

- Đây là proxy chạy trong sandbox: máy bị recycle là proxy tắt (bật lại bằng lệnh trên),
  và IP ra ngoài sẽ thay đổi — hostname + port giữ nguyên khi còn sống.
- IP là datacenter: Google/Netflix… có thể hỏi captcha — bản chất đường đi, không phải lỗi cấu hình.
- Ai có user/pass là dùng được — không share lung tung; nghi ngờ lộ thì đổi mật khẩu.
- Cần IP cố định chạy thật 24/7: dùng `vps-install.sh` trên VPS (mục dưới).

## Chạy trên VPS (IP cố định, 24/7)

```bash
sudo bash proxy/vps-install.sh        # mặc định cổng 8080
# hoặc: sudo bash proxy/vps-install.sh 9080
```

Script tự: tải gost → tạo user/pass → dựng service systemd → mở firewall (ufw/iptables),
rồi in ra endpoint `IP:PORT:boss:PASS` để nhập vào BP Proxy Switcher.
Với Oracle Cloud, nhớ mở ingress TCP cho cổng đó trong **VCN Security List**.
