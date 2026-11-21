# Nguồn SQL trích xuất dữ liệu HIS

Tài liệu này lưu năm truy vấn nguồn dùng để tạo các file trong `data/raw/`. Phạm vi thời gian hiện tại là từ `2026-04-01 00:00:00` đến trước `2026-08-01 00:00:00`.

## Lưu ý trước khi chạy

- Cần xác nhận `doi_tuong_kcb = 1` đúng là đối tượng ngoại trú tại hệ thống bệnh viện.
- Truy vấn `01_encounter` lọc chín khoa `152, 153, 155, 156, 157, 158, 159, 160, 161`.
- Các CTE encounter trong truy vấn 03, 04 và 05 hiện chưa lặp lại điều kiện khoa. Đây là SQL gốc và cần được thống nhất phạm vi nếu muốn mọi nguồn có cùng cohort.
- Truy vấn 02 cũng không lọc `active IS TRUE` hoặc khoa; chất lượng liên kết được kiểm tra ở bước audit.
- Dấu phẩy sau `END AS status_name` trong truy vấn 05 đã được bổ sung để câu SQL có thể thực thi.

## 1. `01_encounter.csv`

Grain: một dòng trên một lượt điều trị (`nb_dot_dieu_tri_id`).

```sql
WITH params AS (
    SELECT
        TIMESTAMP '2026-04-01 00:00:00' AS from_time,
        TIMESTAMP '2026-08-01 00:00:00' AS to_time
)

SELECT
    -- Identifier
    ddt.id                              AS nb_dot_dieu_tri_id,
    ddt.nb_thong_tin_id,
    ddt.ma_ho_so,
    ddt.ma_nb,

    -- Patient characteristics
    ddt.gioi_tinh,
    ddt.ngay_sinh,
    ddt.chi_nam_sinh,

    -- Visit / payer
    ddt.doi_tuong,
    ddt.doi_tuong_kcb,
    ddt.loai_doi_tuong_id,
    ldt.ten                             AS ten_loai_doi_tuong,
    ddt.cap_cuu,
    ddt.kham_suc_khoe,

    -- Status
    ddt.trang_thai                      AS trang_thai_dot,

    -- Location
    ddt.khoa_id,
    k.ma                                AS ma_khoa,
    k.ten                               AS ten_khoa,
    ddt.khoa_tiep_don_id,
    ktd.ma                              AS ma_khoa_tiep_don,
    ktd.ten                             AS ten_khoa_tiep_don,

    -- Reception
    ddt.nhan_vien_tiep_don_id,

    -- Time
    ddt.thoi_gian_vao_vien,
    ddt.thoi_gian_ra_vien,

    -- Derived calendar fields
    EXTRACT(HOUR FROM ddt.thoi_gian_vao_vien) AS arrival_hour,
    EXTRACT(DOW FROM ddt.thoi_gian_vao_vien)  AS day_of_week,
    EXTRACT(MONTH FROM ddt.thoi_gian_vao_vien) AS arrival_month,
    CASE
        WHEN EXTRACT(DOW FROM ddt.thoi_gian_vao_vien) IN (0, 6) THEN 1
        ELSE 0
    END AS is_weekend

FROM his.nb_dot_dieu_tri ddt
CROSS JOIN params p
LEFT JOIN his.dm_khoa k
    ON k.id = ddt.khoa_id
LEFT JOIN his.dm_khoa ktd
    ON ktd.id = ddt.khoa_tiep_don_id
LEFT JOIN his.dm_loai_doi_tuong ldt
    ON ldt.id = ddt.loai_doi_tuong_id

WHERE ddt.thoi_gian_vao_vien >= p.from_time
  AND ddt.thoi_gian_vao_vien < p.to_time
  AND ddt.doi_tuong_kcb = 1
  AND ddt.active IS TRUE
  AND ddt.khoa_id IN (152, 153, 155, 156, 157, 158, 159, 160, 161)

ORDER BY ddt.thoi_gian_vao_vien;
```

## 2. `02_reception.csv`

Grain: một dòng trên một lần lấy số tiếp đón (`lay_so_tiep_don_id`).

```sql
WITH params AS (
    SELECT
        TIMESTAMP '2026-04-01 00:00:00' AS from_time,
        TIMESTAMP '2026-08-01 00:00:00' AS to_time
)

SELECT
    ls.id                           AS lay_so_tiep_don_id,
    ls.nb_dot_dieu_tri_id,
    ls.thoi_gian_lay_so,
    ls.thoi_gian_hoan_thanh,
    ls.quay_tiep_don_id,
    ls.khu_vuc_id,
    ddt.ma_ho_so,
    ddt.nb_thong_tin_id,
    ddt.thoi_gian_vao_vien,

    EXTRACT(
        EPOCH FROM (ls.thoi_gian_lay_so - ddt.thoi_gian_vao_vien)
    ) / 60.0 AS vao_vien_den_lay_so_phut,

    EXTRACT(
        EPOCH FROM (ls.thoi_gian_hoan_thanh - ls.thoi_gian_lay_so)
    ) / 60.0 AS lay_so_den_hoan_thanh_phut

FROM his.nb_lay_so_tiep_don ls
JOIN his.nb_dot_dieu_tri ddt
    ON ddt.id = ls.nb_dot_dieu_tri_id
CROSS JOIN params p

WHERE ddt.thoi_gian_vao_vien >= p.from_time
  AND ddt.thoi_gian_vao_vien < p.to_time
  AND ddt.doi_tuong_kcb = 1

ORDER BY
    ls.thoi_gian_lay_so,
    ls.nb_dot_dieu_tri_id;
```

## 3. `03_exam.csv`

Grain: một dòng trên một dịch vụ khám (`nb_dich_vu_id`), với `loai_dich_vu = 10`.

```sql
WITH params AS (
    SELECT
        TIMESTAMP '2026-04-01 00:00:00' AS from_time,
        TIMESTAMP '2026-08-01 00:00:00' AS to_time
),

encounter_ids AS (
    SELECT ddt.id
    FROM his.nb_dot_dieu_tri ddt
    CROSS JOIN params p
    WHERE ddt.thoi_gian_vao_vien >= p.from_time
      AND ddt.thoi_gian_vao_vien < p.to_time
      AND ddt.doi_tuong_kcb = 1
      AND ddt.active IS TRUE
)

SELECT
    -- Keys
    dv.id                              AS nb_dich_vu_id,
    dv.nb_dot_dieu_tri_id,

    -- Service
    dv.dich_vu_id,
    ddv.ma                             AS ma_dich_vu,
    ddv.ten                            AS ten_dich_vu,
    dv.loai_dich_vu,
    ddv.nhom_dich_vu_cap1_id,
    ddv.nhom_dich_vu_cap2_id,
    ddv.nhom_dich_vu_cap3_id,

    -- Timestamp
    dv.thoi_gian_chi_dinh,
    kt.thoi_gian_tiep_nhan,
    dv.thoi_gian_thuc_hien,

    -- Queue
    kt.stt,
    kt.trang_thai,
    kt.uu_tien,
    kt.cap_cuu,

    -- Not performed / abandonment evidence
    kt.khong_thuc_hien,
    kt.ly_do_khong_thuc_hien,
    kt.thoi_gian_xac_nhan_khong_thuc_hien,

    -- Room / department
    kt.phong_thuc_hien_id,
    phong.ma                           AS ma_phong_thuc_hien,
    phong.ten                          AS ten_phong_thuc_hien,
    phong.dia_diem                     AS dia_diem_phong,
    phong.thoi_gian_thuc_hien_trung_binh,
    phong.khoa_id                      AS khoa_thuc_hien_id,
    khoa.ma                            AS ma_khoa_thuc_hien,
    khoa.ten                           AS ten_khoa_thuc_hien,

    -- Doctor
    kham.bac_si_kham_id,
    bsk.ma                             AS ma_bac_si_kham,
    kham.bac_si_ket_luan_id,
    bskl.ma                            AS ma_bac_si_ket_luan,
    kham.stt_chuyen_khoa,
    kham.dot_kham_moi,

    -- Conclusion
    kl.thoi_gian_ket_luan,
    kl.huong_dieu_tri,
    kl.ket_qua_dieu_tri,

    -- Follow-up: không dùng làm input cho waiting model
    kl.thoi_gian_hen_kham,

    -- Service tree
    dv.chi_dinh_tu_dich_vu_id,
    dv.chi_dinh_tu_loai_dich_vu,

    -- Administration
    dv.doi_tuong_kcb,
    dv.loai_doi_tuong_id,
    dv.trang_thai_hoan,
    dv.thanh_toan

FROM encounter_ids enc
JOIN his.nb_dich_vu dv
    ON dv.nb_dot_dieu_tri_id = enc.id
JOIN his.nb_dv_ky_thuat kt
    ON kt.id = dv.id
JOIN his.nb_dv_kham kham
    ON kham.id = dv.id
LEFT JOIN his.nb_dv_kham_ket_luan kl
    ON kl.id = kham.id
LEFT JOIN his.dm_dich_vu ddv
    ON ddv.id = dv.dich_vu_id
LEFT JOIN his.dm_phong phong
    ON phong.id = kt.phong_thuc_hien_id
LEFT JOIN his.dm_khoa khoa
    ON khoa.id = phong.khoa_id
LEFT JOIN his.dm_nhan_vien bsk
    ON bsk.id = kham.bac_si_kham_id
LEFT JOIN his.dm_nhan_vien bskl
    ON bskl.id = kham.bac_si_ket_luan_id

WHERE dv.loai_dich_vu = 10

ORDER BY
    dv.nb_dot_dieu_tri_id,
    dv.thoi_gian_chi_dinh,
    dv.id;
```

## 4. `04_service_journey.csv`

Grain: một dòng trên một dịch vụ trong hành trình điều trị (`nb_dich_vu_id`). Tập này gồm nhiều loại dịch vụ.

```sql
WITH params AS (
    SELECT
        TIMESTAMP '2026-04-01 00:00:00' AS from_time,
        TIMESTAMP '2026-08-01 00:00:00' AS to_time
),

encounter_ids AS (
    SELECT ddt.id
    FROM his.nb_dot_dieu_tri ddt
    CROSS JOIN params p
    WHERE ddt.thoi_gian_vao_vien >= p.from_time
      AND ddt.thoi_gian_vao_vien < p.to_time
      AND ddt.doi_tuong_kcb = 1
      AND ddt.active IS TRUE
)

SELECT
    -- Key
    dv.id                              AS nb_dich_vu_id,
    dv.nb_dot_dieu_tri_id,

    -- Service
    dv.dich_vu_id,
    ddv.ma                             AS ma_dich_vu,
    ddv.ten                            AS ten_dich_vu,
    dv.loai_dich_vu,
    ddv.nhom_dich_vu_cap1_id,
    n1.ma                              AS ma_nhom_dv_cap1,
    n1.ten                             AS ten_nhom_dv_cap1,
    ddv.nhom_dich_vu_cap2_id,
    n2.ma                              AS ma_nhom_dv_cap2,
    n2.ten                             AS ten_nhom_dv_cap2,
    ddv.nhom_dich_vu_cap3_id,
    n3.ma                              AS ma_nhom_dv_cap3,
    n3.ten                             AS ten_nhom_dv_cap3,

    -- Process timestamps
    dv.thoi_gian_chi_dinh,
    kt.thoi_gian_tiep_nhan,
    dv.thoi_gian_thuc_hien,
    CASE
        WHEN dv.loai_dich_vu = 10 THEN kl.thoi_gian_ket_luan
        WHEN dv.loai_dich_vu = 20 THEN xn.thoi_gian_co_ket_qua
        WHEN dv.loai_dich_vu IN (30, 40) THEN cdha.thoi_gian_co_ket_qua
        ELSE NULL
    END AS thoi_gian_co_ket_qua,

    -- Queue / status
    kt.stt,
    kt.trang_thai,
    kt.uu_tien,
    kt.cap_cuu,

    -- Non-completion
    kt.khong_thuc_hien,
    kt.ly_do_khong_thuc_hien,
    kt.thoi_gian_xac_nhan_khong_thuc_hien,

    -- Location
    dv.khoa_chi_dinh_id,
    kt.phong_thuc_hien_id,
    phong.ma                            AS ma_phong_thuc_hien,
    phong.ten                           AS ten_phong_thuc_hien,
    phong.khoa_id                       AS khoa_thuc_hien_id,
    khoa.ma                             AS ma_khoa_thuc_hien,
    khoa.ten                            AS ten_khoa_thuc_hien,

    -- Staff
    dv.bac_si_chi_dinh_id,

    -- Service parent
    dv.chi_dinh_tu_dich_vu_id,
    dv.chi_dinh_tu_loai_dich_vu,

    -- Payment
    dv.phieu_thu_id,
    pt.thoi_gian_thanh_toan,
    dv.thanh_toan,
    dv.trang_thai_hoan

FROM encounter_ids enc
JOIN his.nb_dich_vu dv
    ON dv.nb_dot_dieu_tri_id = enc.id
LEFT JOIN his.nb_dv_ky_thuat kt
    ON kt.id = dv.id
LEFT JOIN his.nb_dv_kham_ket_luan kl
    ON kl.id = dv.id
LEFT JOIN his.nb_dv_xet_nghiem xn
    ON xn.id = dv.id
LEFT JOIN his.nb_dv_cdha_tdcn_pt_tt cdha
    ON cdha.id = dv.id
LEFT JOIN his.dm_dich_vu ddv
    ON ddv.id = dv.dich_vu_id
LEFT JOIN his.dm_nhom_dich_vu_cap1 n1
    ON n1.id = ddv.nhom_dich_vu_cap1_id
LEFT JOIN his.dm_nhom_dich_vu_cap2 n2
    ON n2.id = ddv.nhom_dich_vu_cap2_id
LEFT JOIN his.dm_nhom_dich_vu_cap3 n3
    ON n3.id = ddv.nhom_dich_vu_cap3_id
LEFT JOIN his.dm_phong phong
    ON phong.id = kt.phong_thuc_hien_id
LEFT JOIN his.dm_khoa khoa
    ON khoa.id = phong.khoa_id
LEFT JOIN his.nb_phieu_thu pt
    ON pt.id = dv.phieu_thu_id

ORDER BY
    dv.nb_dot_dieu_tri_id,
    COALESCE(
        kt.thoi_gian_tiep_nhan,
        dv.thoi_gian_thuc_hien,
        dv.thoi_gian_chi_dinh
    ),
    dv.id;
```

## 5. `05_exam_status_log.csv`

Grain: một dòng trên một lần trạng thái thực sự thay đổi của một dịch vụ khám.

```sql
WITH params AS (
    SELECT
        TIMESTAMP '2026-04-01 00:00:00' AS from_time,
        TIMESTAMP '2026-08-01 00:00:00' AS to_time
),

exam_ids AS (
    SELECT
        dv.id AS nb_dich_vu_id,
        dv.nb_dot_dieu_tri_id
    FROM his.nb_dot_dieu_tri ddt
    JOIN his.nb_dich_vu dv
        ON dv.nb_dot_dieu_tri_id = ddt.id
    CROSS JOIN params p
    WHERE ddt.thoi_gian_vao_vien >= p.from_time
      AND ddt.thoi_gian_vao_vien < p.to_time
      AND ddt.doi_tuong_kcb = 1
      AND ddt.active IS TRUE
      AND dv.loai_dich_vu = 10
),

log_with_previous AS (
    SELECT
        l.id AS nb_dich_vu_id,
        e.nb_dot_dieu_tri_id,
        l.thoi_gian_thay_doi_ban_ghi,
        l.trang_thai,
        l.tai_khoan,
        l.api,
        l.uri,
        LAG(l.trang_thai) OVER (
            PARTITION BY l.id
            ORDER BY l.thoi_gian_thay_doi_ban_ghi
        ) AS previous_status
    FROM exam_ids e
    JOIN his.log_nb_dv_ky_thuat l
        ON l.id = e.nb_dich_vu_id
)

SELECT
    nb_dich_vu_id,
    nb_dot_dieu_tri_id,
    thoi_gian_thay_doi_ban_ghi,
    previous_status,
    trang_thai,
    CASE
        WHEN trang_thai = 20 THEN 'Chờ khám'
        WHEN trang_thai = 30 THEN 'Chờ khám'
        WHEN trang_thai = 40 THEN 'Chờ khám'
        WHEN trang_thai = 50 THEN 'Bỏ qua'
        WHEN trang_thai = 60 THEN 'Đang khám'
        WHEN trang_thai = 70 THEN 'Đang thực hiện DV'
        WHEN trang_thai = 100 THEN 'Chờ kết luận'
        WHEN trang_thai = 110 THEN 'Chờ kết luận'
        WHEN trang_thai = 120 THEN 'Chờ kết luận'
        WHEN trang_thai = 130 THEN 'Bỏ qua KL khám'
        WHEN trang_thai = 140 THEN 'Đang kết luận'
        WHEN trang_thai = 150 THEN 'Đã kết luận'
        ELSE 'OTHER'
    END AS status_name,
    tai_khoan,
    api,
    uri

FROM log_with_previous

-- Chỉ giữ thời điểm trạng thái thực sự thay đổi.
WHERE previous_status IS DISTINCT FROM trang_thai
   OR previous_status IS NULL

ORDER BY
    nb_dich_vu_id,
    thoi_gian_thay_doi_ban_ghi;
```

## File đầu ra tương ứng

Sau khi chạy từng truy vấn, xuất đúng tên sau vào `data/raw/`:

```text
01_encounter.csv
02_reception.csv
03_exam.csv
04_service_journey.csv
05_exam_status_log.csv
```

Không đổi tên cột trong lúc export vì các script trong `pipeline/` sử dụng trực tiếp schema trên.

## 6. Truy vấn snapshot tùy chọn cho dự báo trực tiếp

Truy vấn PostgreSQL tùy chọn để lấy các ca vừa vào đúng status hàng đợi được lưu tại [`sql/queue_snapshot_for_prediction.sql`](sql/queue_snapshot_for_prediction.sql). Output không chứa status `60`, target hoặc trường phát sinh sau thời điểm dự báo.

Truy vấn mặc định lấy cửa sổ năm phút theo dạng nửa kín `[from_time, to_time)`. Nếu sử dụng định kỳ, cần truyền hoặc lưu checkpoint `to_time` của lần chạy trước để các cửa sổ không hở và không chồng lặp. Một dịch vụ có thể vào lại hàng đợi; cặp `nb_dich_vu_id` + `queue_entry_time` là khóa sự kiện.
