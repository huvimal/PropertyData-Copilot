# Hướng dẫn PropertyData Copilot

## 1. Cài môi trường riêng và mở app

```powershell
cd "C:\Users\LOQ\Documents\Codex\2026-11-06\cj\outputs\PropertyData-Copilot"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port 8511
```

Mở http://127.0.0.1:8511. Không cần API key cho quy trình mẫu. Code đã được kiểm tra bằng môi trường
Huni Catalog AI tương thích; các lệnh trên tạo môi trường riêng. Sau lần đầu chỉ cần lệnh mở app.

## 2. Nạp và chuẩn hóa mẫu

1. Bấm **Nạp 16 tin mẫu**.
2. Bấm **Chuẩn hóa tin pending bằng quy tắc**.
3. Chọn R001. Xem nguồn và JSON bên cạnh.

R001 cần có purpose=sale, property_type=apartment, city=ho_chi_minh, area_name=an phú,
bedrooms=2, area_m2="70", price_vnd="2500000000", price_basis=total.
Đây là dữ liệu và giá giả lập, không mô tả thị trường hiện tại.

## 3. Duyệt ba tin để thử ranh giới ngân sách

Với R001, nhập tên, ghi chú như “Đã đối chiếu nguồn, giá tổng VND, diện tích và ứng viên trùng.”,
tích xác nhận rồi **Duyệt tin đã lưu**.

Lặp lại với R002 và R003. R001/R002 có nguồn giống nhau: đây là ví dụ ứng viên trùng; ứng dụng không tự
xóa chúng. Bạn vẫn có thể duyệt cả hai để xem cách tìm kiếm xử lý danh mục hiện tại.

R003 có giá đúng 3 tỷ. Sau ba lần duyệt, bộ tìm kiếm có ba tin đủ điều kiện để kiểm tra giới hạn giá.
Chưa duyệt tin thì nó không xuất hiện trong kết quả, dù có giá/điểm xếp hạng phù hợp.

## 4. Tìm bằng câu hỏi và xác nhận bộ lọc

Giữ câu mẫu:

Mua căn hộ An Phú TP.HCM dưới 3 tỷ, ít nhất 2 phòng ngủ, từ 65 m2

1. Bấm **Đề xuất bộ lọc bằng quy tắc**.
2. Kiểm tra purpose=sale; apartment; ho_chi_minh; an phú; min_bedrooms=2; min_area=65;
   budget=3000000000; budget_operator=lt.
3. Bấm **Xác nhận bộ lọc và tìm**.
4. Nếu đã duyệt đúng R001/R002/R003, kết quả phải gồm R001 và R002.
5. Đổi budget_operator thành lte, xác nhận lại: kết quả có thêm R003.

lt là <; lte là ≤; gte là ≥; gt là >. Các số tiền ở form xác nhận là VND, không gõ "3 tỷ" trong ô này.
Từ khóa không nằm trong schema chỉ ảnh hưởng xếp hạng TF-IDF, không bảo đảm tiện ích ngoài bộ lọc.
Thay câu hỏi hoặc tạo đề xuất mới sẽ yêu cầu xác nhận lại; AI không tự thực hiện tìm kiếm.

## 5. Kiểm tra tiền thuê và giá/m²

R005: cho thuê, 15 triệu/tháng. Chỉ khi chọn rent mới so ngân sách với monthly_rent_vnd.
Không so 15 triệu tiền thuê tháng với ngân sách mua nhà.

R007: bán, 35 triệu/m², 85 m². sale_total_vnd = 35000000 × 85 = 2975000000;
price_is_derived=True. Đây là phép nhân nguồn, không phải giá tổng được in, định giá thị trường hoặc tổng
thanh toán có thuế/phí. Duyệt R007 nếu muốn tìm tin Bình An dưới 3 tỷ.

R008/R009 thiếu diện tích/phòng ngủ nên bị loại khi bộ lọc cần trường đó.
R011 giá thỏa thuận để trống; bị loại khi có ngân sách.
R012 giá thuê chưa có đơn vị tháng nên chưa duyệt được khi vẫn giữ giá mơ hồ đó.
R013 mâu thuẫn giá tiêu đề/nội dung nên giá để trống. R014 dùng 2.500 triệu bị coi là mơ hồ.
R015 không có purpose/property_type rõ ràng nên không thể duyệt chỉ vì có con số trong câu chỉ dẫn.

## 6. Sửa và xuất

Sửa JSON theo nguồn rồi bấm **Lưu sửa đổi — hủy duyệt cũ**. Cần duyệt lại mới được tìm hoặc xuất.
Giá/diện tích là chuỗi số, ví dụ "2500000000", "70"; dùng null cho giá trị chưa biết.
bedrooms là số nguyên và có thể bằng 0 cho studio.

Bấm **Tải ZIP tin đã duyệt**. ZIP có reviewed-listings.csv và audit.json. Đóng/mở app vẫn giữ dữ liệu
SQLite trong private/property.sqlite3. Nhập lại CSV cùng nội dung không tự đặt lại trạng thái duyệt.

Ảnh gắn vào tin chỉ dùng dHash để gợi ý tin trùng. Ảnh giống nhau không chứng minh cùng căn nhà;
ảnh khác màu hoặc ít chi tiết có thể trùng hash. Không có thao tác tự ghép/xóa tin.

## 7. Thử Groq tùy chọn

Nhập key và model trong thanh bên, tích đồng ý gửi văn bản. Chọn một tin rồi bấm
**Trích xuất văn bản tin này bằng AI**. Kiểm tra evidence và đơn vị, sửa/lưu/duyệt như bình thường.
Hoặc bấm **Đề xuất bộ lọc bằng AI**, kiểm tra toàn bộ form rồi mới xác nhận.

Mỗi lần bấm chỉ gửi một tin hoặc câu hỏi. Không gửi ảnh, không lưu key, không tự retry. Lỗi 401/403/404
cần kiểm tra key/model/quyền; 429 cần chờ hạn mức. Chưa có benchmark độ chính xác AI thật cho dự án này.

## 8. Lưu báo cáo kiểm tra

```powershell
cd "C:\Users\LOQ\Documents\Codex\2026-11-06\cj\outputs\PropertyData-Copilot"
.\.venv\Scripts\python.exe -X utf8 evaluate.py --output reports/my-evaluation-v1.json
Get-Content reports/my-evaluation-v1.json -Encoding utf8
```

Kỳ vọng listing_cases=16, exact_listing_cases=16, query_cases=4, exact_query_cases=4.
Đây là dữ liệu giả lập và quy tắc có nhãn viết cùng nhau, không phải AI accuracy 100%.
File đã tồn tại thì chọn tên mới. Trước portfolio, chụp nguồn/thuộc tính, ứng viên trùng và bộ lọc/kết quả;
không chụp API key, thông tin liên hệ hoặc dữ liệu khách hàng.
