/*
Purpose
-------
Return leakage-safe queue-entry events for optional direct prediction. Run
every 5 minutes or with explicit non-overlapping from_time/to_time values.

Database dialect: PostgreSQL.
HIS timestamps are treated as local timestamps without time zone.

Important
---------
This query reads current values from nb_dv_ky_thuat. Run it close to the queue
event time. It must not be used for historical backfill unless the hospital has
temporal history for room, priority, emergency flag, and queue number.
*/

WITH params AS (
    SELECT
        LOCALTIMESTAMP - INTERVAL '5 minutes' AS from_time,
        LOCALTIMESTAMP                        AS to_time

        /* For a controlled/replayable batch, replace the two lines above with:
        TIMESTAMP '2026-09-06 08:00:00' AS from_time,
        TIMESTAMP '2026-09-06 08:05:00' AS to_time
        */
),

/* Limit the expensive history window function to services that have a new
   queue-related log row in this batch. */
candidate_service_ids AS (
    SELECT DISTINCT l.id AS nb_dich_vu_id
    FROM his.log_nb_dv_ky_thuat l
    CROSS JOIN params p
    WHERE l.thoi_gian_thay_doi_ban_ghi >= p.from_time
      AND l.thoi_gian_thay_doi_ban_ghi <  p.to_time
      AND l.trang_thai IN (20, 40)
),

/* Read all prior statuses for candidate services so LAG is still correct when
   the previous status occurred before from_time. */
log_with_previous AS (
    SELECT
        l.id AS nb_dich_vu_id,
        l.thoi_gian_thay_doi_ban_ghi AS queue_entry_time,
        l.trang_thai AS queue_entry_status,
        LAG(l.trang_thai) OVER (
            PARTITION BY l.id
            ORDER BY l.thoi_gian_thay_doi_ban_ghi
        ) AS previous_status
    FROM his.log_nb_dv_ky_thuat l
    JOIN candidate_service_ids c
      ON c.nb_dich_vu_id = l.id
),

/* Keep genuine state transitions only. DISTINCT protects the model input from
   duplicate log rows with the same service, timestamp, and status. */
changed_queue_events AS (
    SELECT DISTINCT
        l.nb_dich_vu_id,
        l.queue_entry_time,
        l.queue_entry_status
    FROM log_with_previous l
    CROSS JOIN params p
    WHERE l.queue_entry_time >= p.from_time
      AND l.queue_entry_time <  p.to_time
      AND l.queue_entry_status IN (20, 40)
      AND (
          l.previous_status IS DISTINCT FROM l.queue_entry_status
          OR l.previous_status IS NULL
      )
),

/* Snapshot fields used by the frozen model. No status 60, outcome, conclusion,
   doctor assignment, or other post-queue feature is selected. */
exam_snapshot AS (
    SELECT
        dv.id AS nb_dich_vu_id,
        dv.nb_dot_dieu_tri_id,
        dv.thoi_gian_chi_dinh,
        ddv.ma AS ma_dich_vu,
        phong.ma AS ma_phong_thuc_hien,
        khoa.ma AS ma_khoa_thuc_hien,
        kt.uu_tien,
        kt.cap_cuu,
        kt.stt,
        CASE
            WHEN UPPER(TRIM(COALESCE(ddv.ma, ''))) = 'KB008'
              OR UPPER(TRIM(COALESCE(phong.ma, ''))) = 'H401'
            THEN 20
            ELSE 40
        END AS required_queue_status
    FROM his.nb_dich_vu dv
    JOIN his.nb_dot_dieu_tri ddt
      ON ddt.id = dv.nb_dot_dieu_tri_id
    JOIN his.nb_dv_ky_thuat kt
      ON kt.id = dv.id
    JOIN his.nb_dv_kham kham
      ON kham.id = dv.id
    LEFT JOIN his.dm_dich_vu ddv
      ON ddv.id = dv.dich_vu_id
    LEFT JOIN his.dm_phong phong
      ON phong.id = kt.phong_thuc_hien_id
    LEFT JOIN his.dm_khoa khoa
      ON khoa.id = phong.khoa_id
    JOIN candidate_service_ids c
      ON c.nb_dich_vu_id = dv.id
    WHERE ddt.doi_tuong_kcb = 1
      AND ddt.active IS TRUE
      AND dv.loai_dich_vu = 10
)

SELECT
    e.nb_dich_vu_id,
    e.nb_dot_dieu_tri_id,
    q.queue_entry_time,
    e.ma_dich_vu,
    e.ma_phong_thuc_hien,
    e.ma_khoa_thuc_hien,
    q.queue_entry_status,
    e.uu_tien,
    e.cap_cuu,
    e.stt,
    EXTRACT(
        EPOCH FROM (q.queue_entry_time - e.thoi_gian_chi_dinh)
    ) / 60.0 AS order_to_queue_minutes
FROM changed_queue_events q
JOIN exam_snapshot e
  ON e.nb_dich_vu_id = q.nb_dich_vu_id
WHERE q.queue_entry_status = e.required_queue_status
ORDER BY
    q.queue_entry_time,
    e.nb_dich_vu_id;
