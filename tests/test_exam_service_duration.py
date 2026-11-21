from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from pipeline.audit_and_build_exam_target import build_target


class ExamServiceDurationTests(unittest.TestCase):
    def test_duration_uses_first_status_150_after_first_status_60(self) -> None:
        status = pd.DataFrame(
            [
                (1, 101, '2026-05-01 07:40:00', None, 150, 'completed'),
                (1, 101, '2026-05-01 07:50:00', 20, 40, 'waiting'),
                (1, 101, '2026-05-01 08:00:00', 40, 60, 'in service'),
                (1, 101, '2026-05-01 08:30:00', 60, 150, 'completed'),
                (1, 101, '2026-05-01 08:45:00', 150, 150, 'completed'),
                (2, 102, '2026-05-01 07:55:00', 20, 40, 'waiting'),
                (2, 102, '2026-05-01 08:05:00', 40, 60, 'in service'),
                (3, 103, '2026-05-01 07:50:00', 20, 40, 'waiting'),
                (3, 103, '2026-05-01 07:55:00', 40, 150, 'completed'),
                (3, 103, '2026-05-01 08:10:00', 150, 60, 'in service'),
            ],
            columns=[
                'nb_dich_vu_id',
                'nb_dot_dieu_tri_id',
                'thoi_gian_thay_doi_ban_ghi',
                'previous_status',
                'trang_thai',
                'status_name',
            ],
        )
        exam = pd.DataFrame(
            {
                'nb_dich_vu_id': [1, 2, 3],
                'nb_dot_dieu_tri_id': [101, 102, 103],
                'dich_vu_id': [1001, 1001, 1001],
                'ma_dich_vu': ['DV1', 'DV1', 'DV1'],
                'ten_dich_vu': ['Exam', 'Exam', 'Exam'],
                'thoi_gian_chi_dinh': ['2026-05-01 07:00:00'] * 3,
                'stt': [1, 2, 3],
                'uu_tien': [False, False, False],
                'cap_cuu': [False, False, False],
                'phong_thuc_hien_id': [10, 10, 10],
                'ma_phong_thuc_hien': ['P1', 'P1', 'P1'],
                'ten_phong_thuc_hien': ['Room 1', 'Room 1', 'Room 1'],
                'khoa_thuc_hien_id': [20, 20, 20],
                'ma_khoa_thuc_hien': ['K1', 'K1', 'K1'],
                'ten_khoa_thuc_hien': ['Department 1'] * 3,
                'bac_si_kham_id': [30, 30, 30],
                'ma_bac_si_kham': ['D1', 'D1', 'D1'],
            }
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status_path = root / '05_exam_status_log.csv'
            exam_path = root / '03_exam.csv'
            status.to_csv(status_path, index=False)
            exam.to_csv(exam_path, index=False)
            target, _, _ = build_target(
                {
                    '05_exam_status_log': status_path,
                    '03_exam': exam_path,
                }
            )

        by_id = target.set_index('nb_dich_vu_id')
        self.assertEqual(
            by_id.loc[1, 'exam_complete_time'],
            pd.Timestamp('2026-05-01 08:30:00'),
        )
        self.assertAlmostEqual(by_id.loc[1, 'service_minutes_60_to_150'], 30.0)
        self.assertTrue(bool(by_id.loc[1, 'service_duration_valid_60_to_150']))

        self.assertTrue(pd.isna(by_id.loc[2, 'exam_complete_time']))
        self.assertFalse(bool(by_id.loc[2, 'has_status_150_any']))
        self.assertFalse(bool(by_id.loc[2, 'service_duration_valid_60_to_150']))

        self.assertTrue(bool(by_id.loc[3, 'has_status_150_any']))
        self.assertFalse(bool(by_id.loc[3, 'has_status_150_after_60']))
        self.assertTrue(pd.isna(by_id.loc[3, 'service_minutes_60_to_150']))


if __name__ == '__main__':
    unittest.main()
