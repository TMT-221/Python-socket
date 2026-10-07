# SocketRoom — Hướng dẫn quản trị viên

Áp dụng cho bản SocketRoom hoàn chỉnh đã sửa launcher chạy nền, có các file `start_public_background.ps1` và `socketroom_process.ps1`.

Quản trị viên phụ trách bật máy chủ, gửi link HTTPS, quản lý phòng và sao lưu dữ liệu. Quyền quản trị trên giao diện được cấp bằng mã trong `admin_secret.txt` trên máy chủ.

## 1. Chuẩn bị

- Máy Windows có Python 3.11 trở lên.
- Giải nén đầy đủ chương trình vào một thư mục cố định.
- Trong thư mục cần có `start_public.bat`, `stop_public.bat`, các file Python, thư mục `static` và `tools/cloudflared.exe`.
- Máy có Internet. Giữ máy bật và tránh Sleep trong thời gian chia sẻ.

Các file dữ liệu cần giữ:

| File | Công dụng |
| --- | --- |
| `socketroom.db` | Phòng và lịch sử tin nhắn. |
| `admin_secret.txt` | Mã đăng nhập quản trị; được tạo khi chạy lần đầu nếu chưa có. |
| `public_link.txt` | Link HTTPS của phiên đang chạy; có thể thay đổi khi chạy lại. |
| `logs/` | Log để kiểm tra lỗi khởi động và kết nối. |

Chỉ gửi link chat cho người tham gia. Giữ mã quản trị và bản sao cơ sở dữ liệu trong phạm vi người quản lý; phòng công khai cho phép người có link xem lịch sử.

## 2. Bật máy chủ và lấy link HTTPS

1. Mở thư mục SocketRoom cần dùng.
2. Bấm đúp **start_public.bat**.
3. Đợi thông báo **LINK PHÒNG CHAT**. DNS của link mới đôi khi cần vài phút để cập nhật; chương trình chỉ báo sẵn sàng sau khi kiểm tra HTTPS thành công.
4. Mở **public_link.txt** trong cùng thư mục, sao chép link HTTPS và gửi cho người tham gia.

Sau khi link sẵn sàng, bạn có thể đóng cửa sổ khởi động. SocketRoom và Cloudflare Tunnel tiếp tục chạy nền. Bấm `start_public.bat` lần nữa sẽ dùng lại phiên đang hoạt động trong cùng thư mục.

- **Link HTTPS public**: dùng để truy cập từ máy khác hoặc điện thoại.
- **http://127.0.0.1:8080**: dùng để truy cập cục bộ trên chính máy chủ.

Mỗi lần chạy lại, lấy link hiện tại trong `public_link.txt` và gửi lại cho người dùng. Các link phòng được sao chép từ phiên cũ cũng cần được chia sẻ lại.

## 3. Đăng nhập quản trị

1. Mở trang chat trên máy chủ tại **http://127.0.0.1:8080**, hoặc mở link HTTPS đang hoạt động.
2. Nhập tên hiển thị, bấm **Vào phòng chat**.
3. Bấm **Đăng nhập quản trị**.
4. Mở `admin_secret.txt` trên máy chủ bằng Notepad, sao chép mã và dán vào ô **Mã quản trị**.
5. Bấm **Đăng nhập**. Khi thành công, giao diện hiển thị trạng thái quản trị và nút xóa phòng.

Phiên quản trị có thời hạn 12 giờ và hết hiệu lực khi máy chủ web khởi động lại. Phiên gắn với trình duyệt và địa chỉ đang dùng; khi chuyển giữa địa chỉ cục bộ và link public, đăng nhập quản trị tại địa chỉ mới nếu cần.

## 4. Tạo và xóa phòng

### Tạo phòng

Bấm **+ Tạo phòng**, nhập tên và mô tả, giữ **Chat server** ở **Tự động phân bổ**, rồi tạo phòng. Người dùng thông thường cũng được tạo phòng công khai.

### Xóa phòng

1. Đăng nhập quản trị.
2. Chọn đúng phòng cần xóa.
3. Bấm biểu tượng **thùng rác** ở tiêu đề phòng.
4. Trong hộp xác nhận, nhập **chính xác tên phòng đang hiển thị**, không thêm dấu `#` ở đầu.
5. Bấm **Xóa phòng**.

**Thao tác này xóa phòng và toàn bộ tin nhắn của phòng khỏi cơ sở dữ liệu. Giao diện không có nút hoàn tác.** Sao lưu trước nếu cần giữ lịch sử.

Khi xóa hết phòng, danh sách sẽ trống kể cả sau khi khởi động lại. Tạo phòng mới từ giao diện để tiếp tục sử dụng.

## 5. Đăng xuất, dừng và chạy lại

### Đăng xuất quản trị

Bấm **Đăng xuất** ở cuối cột bên trái. Khi kết nối thành công, máy chủ thu hồi phiên quản trị; trình duyệt xóa tên đã nhớ và trở về màn hình nhập tên. Tin nhắn đã lưu vẫn còn.

Nếu ứng dụng báo chưa xác nhận được thoát quyền quản trị do mất kết nối, kết nối lại và thực hiện đăng xuất để máy chủ xác nhận.

### Dừng toàn bộ máy chủ

1. Mở **stop_public.bat** trong đúng thư mục SocketRoom đang chạy.
2. Đợi thông báo đã dừng.

Chương trình dừng dịch vụ chat và tunnel, đồng thời xóa file link của phiên. Người dùng sẽ mất truy cập public trong thời gian máy chủ dừng.

### Chạy lại

Mở **start_public.bat**, đợi kiểm tra link thành công rồi gửi link mới trong **public_link.txt**.

## 6. Sao lưu và khôi phục

### Sao lưu

1. Dừng ứng dụng bằng **stop_public.bat**.
2. Sao chép **socketroom.db** vào thư mục sao lưu có ghi ngày giờ.
3. Lưu riêng **admin_secret.txt** nếu cần giữ nguyên mã quản trị; chỉ người quản lý được truy cập bản sao này.
4. Mở **start_public.bat** để tiếp tục dùng và gửi link mới.

### Khôi phục

1. Dừng ứng dụng.
2. Sao lưu thêm file `socketroom.db` hiện tại để giữ bản trước khi khôi phục.
3. Chép bản cơ sở dữ liệu cần khôi phục vào thư mục chương trình, đặt tên **socketroom.db**, thay file cùng tên.
4. Chạy lại bằng **start_public.bat**, kiểm tra phòng và lịch sử, rồi gửi link mới.

Khôi phục đưa **toàn bộ cơ sở dữ liệu** về thời điểm sao lưu. Các thay đổi và tin nhắn phát sinh sau thời điểm đó sẽ không có trong bản khôi phục.

## 7. Xử lý lỗi thường gặp

| Hiện tượng | Cách xử lý |
| --- | --- |
| **Cloudflare Error 1033** | Kiểm tra máy đang bật, có Internet và không Sleep. Chạy `start_public.bat`, đợi kiểm tra HTTPS thành công và gửi link hiện tại trong `public_link.txt`. Nếu tiếp tục lỗi, xem log tunnel. |
| **Cổng 5000/5001/5002/8080 đang được dùng** | Kiểm tra bản SocketRoom khác đang chạy. Dừng bản đó bằng `stop_public.bat` trong đúng thư mục rồi mở bản cần dùng. Nếu cổng thuộc phần mềm khác, xác định phần mềm đó trước khi xử lý. |
| **Không tạo được link / link chưa sẵn sàng** | Đợi thông báo cuối cùng, kiểm tra Internet, file `tools/cloudflared.exe` và các log khởi động. Khi cần thử lại, dừng bằng `stop_public.bat` rồi mở lại. |
| **Mã quản trị không đúng** | Dùng mã trong `admin_secret.txt` của đúng thư mục đang chạy. Kiểm tra khoảng trắng thừa khi sao chép. |
| **Nút xóa phòng chưa xuất hiện** | Vào bằng tên hiển thị, đăng nhập quản trị tại đúng địa chỉ đang mở và chọn một phòng. |
| **Gửi tin lỗi / không cập nhật** | Thử trang cục bộ và kiểm tra log web, directory, chat nodes. Nếu trang cục bộ hoạt động nhưng public lỗi, kiểm tra tunnel và link hiện tại. |

Log cần xem bằng Notepad:

- `logs/launcher.log`: tiến trình khởi động, tạo link và trạng thái chạy.
- `logs/launcher_error.log`: lỗi launcher.
- `logs/tunnel.log`: kết nối Cloudflare.
- `logs/web.log`, `logs/directory.log`, `logs/chat1.log`, `logs/chat2.log`: các dịch vụ chat.

Cloudflare xác định lỗi **1033** là không tìm thấy tiến trình `cloudflared` kết nối khỏe để nhận lưu lượng. Xem [hướng dẫn chính thức của Cloudflare](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1033/).

## 8. Tự chạy sau khi đăng nhập Windows — tùy chọn

Mặc định bạn mở **start_public.bat** thủ công mỗi lần cần dùng.

Nếu muốn bật tự chạy, mở PowerShell tại thư mục SocketRoom và chạy:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\enable_autostart.ps1
```

Để tắt tự chạy:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\disable_autostart.ps1
```

Chức năng này chạy khi **đăng nhập Windows**. Sau mỗi lần khởi động, đợi link sẵn sàng rồi lấy link mới từ **public_link.txt**.

