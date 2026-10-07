# SocketRoom — bản hoàn chỉnh

SocketRoom chạy trên máy Windows bằng Python, TCP socket và SQLite. Khi bật chia sẻ, chương trình tạo link HTTPS tạm thời qua Cloudflare Quick Tunnel. Người có link có thể vào phòng công khai, xem lịch sử và gửi tin.

## Chạy bản mới

1. Nếu bản SocketRoom cũ đang chạy, dừng bằng Ctrl+C trong cửa sổ đó hoặc mở stop_public.bat trong thư mục bản cũ. Hai bản không thể cùng dùng các cổng 5000–5002 và 8080.
2. Giải nén toàn bộ socketroom_complete.zip vào một thư mục riêng. Cần Python 3.11 trở lên.
3. Mở start_public.bat trong thư mục mới. Đợi dòng LINK PHÒNG CHAT.
4. Gửi link cho người khác. Mỗi người nhập tên hiển thị của mình. Giữ máy, Internet và cửa sổ chương trình hoạt động.
5. Link được lưu trong public_link.txt khi chương trình đang chạy. Nhấn Ctrl+C hoặc mở stop_public.bat trong thư mục mới để dừng.

Mỗi lần khởi động Quick Tunnel tạo link mới; link cũ hết hiệu lực. Nút ba chấm trong giao diện sao chép link đến phòng đang mở. Nếu chỉ thử trên máy này, chạy python start_public.py --local-only và mở http://127.0.0.1:8080.

## Quyền quản trị và xóa phòng

Lần khởi động đầu, chương trình tự tạo mã quản trị ngẫu nhiên trong admin_secret.txt ở thư mục dự án trên máy chủ. Chỉ chủ máy nên mở file này. Đừng gửi mã cùng link chat, đừng đưa file này lên nơi công khai. File mã quản trị không nằm trong ZIP phát hành.

Trong giao diện, nhập tên hiển thị rồi chọn Đăng nhập quản trị. Dán mã từ admin_secret.txt. Sau đó nút thùng rác xuất hiện ở phòng đang mở. Để xóa, nhập chính xác tên phòng vào hộp xác nhận. Phòng và toàn bộ tin nhắn của phòng bị xóa khỏi SQLite; khách không thể gọi API xóa nếu không có phiên quản trị.

Nút Đăng xuất xóa tên hiển thị lưu trong trình duyệt, thu hồi phiên quản trị của trình duyệt đó và trở lại màn hình nhập tên. Tin nhắn chỉ xóa khỏi khung soạn sau khi máy chủ xác nhận gửi thành công; nếu gửi lỗi, nội dung vẫn còn để thử lại.

## Dữ liệu

Gói này chứa bản sao socketroom.db từ bản sửa trước, giữ các phòng và tin nhắn đã có lúc đóng gói. Các tin mới tiếp tục được lưu trong file này. Giữ thư mục dự án ở vị trí cố định sau khi chạy. Muốn sao lưu, dừng ứng dụng rồi sao chép socketroom.db.

Ai có link đều xem và gửi tin trong các phòng Công khai. Tên hiển thị do người dùng tự nhập; đây chưa phải tài khoản có xác thực. Phòng mẫu “Có mã tham gia” không hiện trên web vì dự án chưa có cơ chế kiểm tra mã. Directory Server và Chat Nodes chỉ lắng nghe trên máy nội bộ.

Khi xóa hết phòng, ứng dụng giữ trạng thái trống sau khi khởi động lại; hãy tạo phòng mới từ giao diện. Link Quick Tunnel là tạm thời và thay đổi mỗi lần chạy.

## Kiểm tra

Bộ kiểm thử mới chạy trên cổng và cơ sở dữ liệu riêng, không đụng phiên chat đang hoạt động:

    python test_full_version.py
    python -m unittest -v test_socketroom test_directory_fixes

Bài kiểm thử đầu cần Chrome và Node.js để kiểm tra giao diện; nó xác minh gửi tin, xóa khung soạn sau khi gửi, quyền quản trị, xóa phòng, đăng xuất, bảo toàn tin của phòng khác và khởi động lại. Nếu không có Chrome/Node.js, các kiểm thử Python còn lại vẫn chạy được. test_web_end_to_end.py là kiểm thử cũ, cần các cổng mặc định còn trống.

## Các file chính

- start_public.bat / start_public.py: chạy cụm dịch vụ và tạo link chia sẻ.
- admin_secret.txt: mã quản trị sinh trên máy chủ khi chạy lần đầu.
- socketroom.db: phòng và lịch sử tin nhắn.
- directory_server.py, chat_server.py, web_server.py: dịch vụ TCP, chat và web.
- static/index.html: giao diện chat.
- tools/cloudflared.exe: chương trình tạo Quick Tunnel đi kèm.

Nếu muốn tự khởi động cùng Windows, chạy powershell -NoProfile -ExecutionPolicy Bypass -File .\enable_autostart.ps1 trong thư mục dự án. Để tắt, chạy powershell -NoProfile -ExecutionPolicy Bypass -File .\disable_autostart.ps1.
