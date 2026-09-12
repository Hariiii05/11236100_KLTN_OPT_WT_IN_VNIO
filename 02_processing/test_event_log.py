from __future__ import annotations

import unittest

import pandas as pd

from prepare_his_events import build_events


class EventLogDeduplicationTests(unittest.TestCase):
    def test_distinct_encounters_with_same_timestamp_are_preserved(self) -> None:
        empty = pd.DataFrame()
        frames = {
            'encounter': pd.DataFrame(
                {
                    'nb_dot_dieu_tri_id': [101, 102],
                    'thoi_gian_vao_vien': ['2026-09-06 08:00:00'] * 2,
                }
            ),
            'reception': empty,
            'journey': empty,
            'status_log': empty,
        }
        events = build_events(frames)
        self.assertEqual(len(events), 2)
        self.assertEqual(set(events['nb_dot_dieu_tri_id']), {101, 102})


if __name__ == '__main__':
    unittest.main()
