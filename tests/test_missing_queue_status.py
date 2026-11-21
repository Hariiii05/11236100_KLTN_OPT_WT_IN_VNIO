from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from pipeline.analyze_missing_queue_status import (
    HAS40,
    MISSING40,
    NO60,
    STATUS20_BRANCH,
    coverage_funnel,
    first_status60_screen,
    holdout_population,
    load_feature_undercount,
    post_stratified_mae,
    prepare_target,
    room_coverage,
    status_trigger_screen,
    within_room_timing,
)


def target_frame() -> pd.DataFrame:
    """Six services: one of each outcome plus one extra missing-status-40 in room B."""
    t = pd.Timestamp
    rows = [
        # id, room, has60, uses20, has40, status20, status40, start, valid, wait
        (1, 'A', True, False, True, t('2026-07-20 08:00'), t('2026-07-20 08:10'), t('2026-07-20 08:30'), True, 20.0),
        (2, 'A', True, False, False, t('2026-07-20 08:05'), pd.NaT, t('2026-07-20 08:50'), False, np.nan),
        (3, 'B', True, False, True, t('2026-07-20 09:00'), t('2026-07-20 09:20'), t('2026-07-20 09:30'), True, 10.0),
        (4, 'B', True, False, False, t('2026-07-20 09:00'), pd.NaT, t('2026-07-20 09:45'), False, np.nan),
        (5, 'H401', True, True, False, t('2026-07-20 10:00'), pd.NaT, t('2026-07-20 10:40'), True, 40.0),
        (6, 'A', False, False, False, t('2026-07-20 10:00'), pd.NaT, pd.NaT, False, np.nan),
    ]
    frame = pd.DataFrame(rows, columns=[
        'nb_dich_vu_id', 'ma_phong_thuc_hien', 'has_status_60_any', 'uses_status20_target',
        'has_status_40_before_60', 'latest_status20_before60_time', 'latest_status40_before60_time',
        'exam_start_time', 'target_valid_v2', 'waiting_minutes'])
    frame['ten_phong_thuc_hien'] = frame['ma_phong_thuc_hien'].map({'A': 'Glôcôm A', 'B': 'Phòng B', 'H401': 'H401'})
    frame['first_status20_time'] = frame['latest_status20_before60_time']
    frame['has_status_40_any'] = frame['has_status_40_before_60']
    return prepare_target(frame)


class GroupingAndCoverageTests(unittest.TestCase):
    def test_groups_follow_the_audit_rule(self) -> None:
        groups = target_frame().set_index('nb_dich_vu_id')['queue_group'].to_dict()
        self.assertEqual(groups, {1: HAS40, 2: MISSING40, 3: HAS40, 4: MISSING40, 5: STATUS20_BRANCH, 6: NO60})

    def test_funnel_adds_up_and_applies_the_wait_limit(self) -> None:
        funnel = dict(zip(*coverage_funnel(target_frame(), 15.0)[['step', 'services']].T.values))
        self.assertEqual(funnel['exam_services_total'], 6)
        self.assertEqual(funnel['missing_required_status40'], 2)
        self.assertEqual(funnel['removed_wait_over_limit_in_status40_branch'], 1)  # 20 > 15 min
        self.assertEqual(funnel['v3_modelling_cohort'], 1)
        self.assertEqual(
            funnel['no_status60'] + funnel['status20_branch_kb008_h401']
            + funnel['missing_required_status40'] + funnel['has_status40_and_status60'], 6)

    def test_room_coverage_keeps_the_two_denominators_apart(self) -> None:
        rooms = room_coverage(target_frame()).set_index('ma_phong_thuc_hien')
        # Room A: 3 services (one never started). 1 of the 2 that started lacks status 40.
        self.assertAlmostEqual(rooms.loc['A', 'missing_status40_rate_of_started'], 0.5)
        self.assertAlmostEqual(rooms.loc['A', 'no_valid_target_rate_of_all'], 2 / 3)
        self.assertTrue(rooms.loc['A', 'is_glaucoma_room'])
        self.assertFalse(rooms.loc['B', 'is_glaucoma_room'])


class TimingTests(unittest.TestCase):
    def test_within_room_timing_needs_enough_services_in_both_groups(self) -> None:
        self.assertTrue(within_room_timing(target_frame(), min_services=2).empty)
        out = within_room_timing(target_frame(), min_services=1).set_index('ma_phong_thuc_hien')
        self.assertAlmostEqual(out.loc['A', 'median_20_to_60_missing'], 45.0)
        self.assertAlmostEqual(out.loc['A', 'median_40_to_60_has_status40'], 20.0)
        self.assertAlmostEqual(out.loc['A', 'ratio_missing_20_to_60_vs_has40_40_to_60'], 45.0 / 20.0)

    def test_first_status60_screen_uses_the_earliest_status60(self) -> None:
        log = pd.DataFrame({
            'nb_dich_vu_id': [1, 1, 2, 2],
            'trang_thai': [60, 60, 60, 20],
            'thoi_gian_thay_doi_ban_ghi': pd.to_datetime(
                ['2026-07-20 08:30', '2026-07-20 08:10', '2026-07-20 09:00', '2026-07-20 08:00']),
            'uri': ['https://sakura.vnio.vn/kham-benh/1?tab=0', 'https://sakura.vnio.vn/do-thi-luc/danh-sach',
                    None, 'https://sakura.vnio.vn/tiep-don/x'],
        })
        screens = first_status60_screen(log).set_index('nb_dich_vu_id')['start_screen'].to_dict()
        self.assertEqual(screens, {1: 'do-thi-luc', 2: '<none>'})


    def test_status_trigger_screen_counts_only_the_requested_status(self) -> None:
        log = pd.DataFrame({
            'trang_thai': [40, 40, 40, 60],
            'uri': ['https://sakura.vnio.vn/thu-ngan/x', 'https://sakura.vnio.vn/thu-ngan/y',
                    'https://sakura.vnio.vn/do-thi-luc/danh-sach', 'https://sakura.vnio.vn/kham-benh/1'],
        })
        out = status_trigger_screen(log, 40).set_index('screen')
        self.assertEqual(out['events'].to_dict(), {'thu-ngan': 2, 'do-thi-luc': 1})
        self.assertAlmostEqual(out.loc['thu-ngan', 'share'], 2 / 3)


class HoldoutReweightingTests(unittest.TestCase):
    def test_reweighting_moves_towards_the_real_room_mix(self) -> None:
        # Room A: 10 observed rows, error 10. Room B: 10 observed rows, error 2.
        observed = pd.DataFrame({
            'nb_dich_vu_id': range(20), 'ma_phong_thuc_hien': ['A'] * 10 + ['B'] * 10,
            'abs_error': [10.0] * 10 + [2.0] * 10})
        # Real mix: A has 10 more services that lack status 40 -> A weighs 20/30.
        population = pd.DataFrame({
            'nb_dich_vu_id': range(30), 'ma_phong_thuc_hien': ['A'] * 20 + ['B'] * 10,
            'queue_group': [HAS40] * 10 + [MISSING40] * 10 + [HAS40] * 10})
        per_room, summary = post_stratified_mae(observed, population, min_observed=5)
        self.assertAlmostEqual(summary['observed_mae'], 6.0)
        self.assertAlmostEqual(summary['post_stratified_mae'], (20 * 10 + 10 * 2) / 30)
        self.assertAlmostEqual(summary['scenarios'][0]['overall_mae'], summary['post_stratified_mae'])
        doubled = next(s for s in summary['scenarios'] if s['mae_multiplier_on_missing_status40_rows'] == 2.0)
        self.assertAlmostEqual(doubled['overall_mae'], (10 * 10 + 10 * 20 + 10 * 2) / 30)
        self.assertEqual(int(per_room.set_index('ma_phong_thuc_hien').loc['A', 'n_missing_status40']), 10)

    def test_rooms_with_too_few_observed_rows_are_reported_not_weighted(self) -> None:
        observed = pd.DataFrame({'nb_dich_vu_id': [1, 2], 'ma_phong_thuc_hien': ['A', 'A'], 'abs_error': [4.0, 6.0]})
        population = pd.DataFrame({
            'nb_dich_vu_id': [1, 2, 3, 4], 'ma_phong_thuc_hien': ['A', 'A', 'C', 'C'],
            'queue_group': [HAS40, HAS40, MISSING40, MISSING40]})
        _, summary = post_stratified_mae(observed, population, min_observed=2)
        self.assertAlmostEqual(summary['post_stratified_mae'], 5.0)
        self.assertAlmostEqual(summary['share_population_in_unrepresented_rooms'], 0.5)

    def test_population_uses_status20_for_rows_without_status40_and_the_same_wait_limit(self) -> None:
        population = holdout_population(target_frame(), '2026-07-20', 60.0)
        # id 2 waits 45 min from status 20 (kept); id 4 waits 45 min (kept); id 5/6 are not status-40 branch.
        self.assertEqual(sorted(population['nb_dich_vu_id']), [1, 2, 3, 4])
        self.assertEqual(sorted(holdout_population(target_frame(), '2026-07-20', 40.0)['nb_dich_vu_id']), [1, 3])
        self.assertTrue(holdout_population(target_frame(), '2026-07-21', None).empty)


class LoadFeatureTests(unittest.TestCase):
    def test_counting_the_missing_services_never_lowers_the_load(self) -> None:
        t = pd.Timestamp
        rows = []
        # Room A: three visible patients and two without status 40 interleaved in time.
        spec = [(1, True, '08:00', '08:40'), (2, False, '08:05', '08:45'), (3, True, '08:10', '08:50'),
                (4, False, '08:12', '08:55'), (5, True, '08:20', '09:00')]
        for service_id, visible, entry, start in spec:
            rows.append({
                'nb_dich_vu_id': service_id, 'ma_phong_thuc_hien': 'A', 'ten_phong_thuc_hien': 'A',
                'has_status_60_any': True, 'uses_status20_target': False, 'has_status_40_before_60': visible,
                'latest_status20_before60_time': t(f'2026-07-20 {entry}') - pd.Timedelta(minutes=1),
                'latest_status40_before60_time': t(f'2026-07-20 {entry}') if visible else pd.NaT,
                'exam_start_time': t(f'2026-07-20 {start}'), 'target_valid_v2': visible, 'waiting_minutes': 30.0,
                'first_status20_time': t(f'2026-07-20 {entry}'), 'has_status_40_any': visible})
        table, summary = load_feature_undercount(prepare_target(pd.DataFrame(rows)), None, window_minutes=30)
        row = table.iloc[0]
        self.assertEqual(summary['cohort_rows_rebuilt'], 3)
        self.assertGreater(row['arrivals_undercount_pct'], 0)
        self.assertGreaterEqual(row['mean_arrivals_30m_all'], row['mean_arrivals_30m_visible'])
        self.assertGreaterEqual(row['mean_patients_ahead_all'], row['mean_patients_ahead_visible'])


if __name__ == '__main__':
    unittest.main()
