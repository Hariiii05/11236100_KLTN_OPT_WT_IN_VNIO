from __future__ import annotations

from pathlib import Path
from collections import Counter
import argparse
import json
import shutil
import subprocess
import zipfile

import numpy as np
import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

RAW_NAMES = [
    '01_encounter.csv',
    '02_reception.csv',
    '03_exam.csv',
    '04_service_journey.csv',
    '05_exam_status_log.csv',
]

META = {
    '01_encounter': {
        'key': ['nb_dot_dieu_tri_id'],
        'date_cols': ['ngay_sinh', 'thoi_gian_vao_vien', 'thoi_gian_ra_vien'],
        'grain': '1 row / encounter (đợt khám)',
    },
    '02_reception': {
        'key': ['lay_so_tiep_don_id'],
        'date_cols': ['thoi_gian_lay_so', 'thoi_gian_hoan_thanh', 'thoi_gian_vao_vien'],
        'grain': '1 row / lượt tiếp đón',
    },
    '03_exam': {
        'key': ['nb_dich_vu_id'],
        'date_cols': [
            'thoi_gian_chi_dinh','thoi_gian_tiep_nhan','thoi_gian_thuc_hien',
            'thoi_gian_xac_nhan_khong_thuc_hien','thoi_gian_ket_luan','thoi_gian_hen_kham'
        ],
        'grain': '1 row / dịch vụ khám',
    },
    '04_service_journey': {
        'key': ['nb_dich_vu_id'],
        'date_cols': [
            'thoi_gian_chi_dinh','thoi_gian_tiep_nhan','thoi_gian_thuc_hien',
            'thoi_gian_co_ket_qua','thoi_gian_xac_nhan_khong_thuc_hien','thoi_gian_thanh_toan'
        ],
        'grain': '1 row / dịch vụ trong hành trình',
    },
    '05_exam_status_log': {
        'key': ['nb_dich_vu_id','thoi_gian_thay_doi_ban_ghi','trang_thai'],
        'date_cols': ['thoi_gian_thay_doi_ban_ghi'],
        'grain': '1 row / lần thay đổi trạng thái dịch vụ khám',
    },
}


def is_xlsx_disguised_as_csv(path: Path) -> bool:
    try:
        return zipfile.is_zipfile(path)
    except Exception:
        return False


def detect_csv_encoding(path: Path) -> str:
    with path.open('rb') as stream:
        sample = stream.read(2_000_000)
    for encoding in ('utf-8-sig', 'utf-8', 'cp1258', 'cp1252'):
        try:
            sample.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    # Some exports contain a UTF-8 BOM plus isolated invalid bytes in text fields.
    # Keep the row structure readable and let the quality report expose the affected values.
    return 'utf-8'


def read_csv(path: Path, **kwargs):
    kwargs.setdefault('encoding', detect_csv_encoding(path))
    kwargs.setdefault('encoding_errors', 'replace')
    return pd.read_csv(path, **kwargs)


def normalize_inputs(raw_dir: Path, work_dir: Path) -> dict[str, Path]:
    """Normalize the five supplied files to real CSVs.

    The supplied files may use .csv names while their bytes are XLSX workbooks.
    If a real CSV already exists in work_dir, it is reused.
    For disguised workbooks, LibreOffice is used only as a conversion fallback.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    out = {}
    for name in RAW_NAMES:
        src = raw_dir / name
        if not src.exists():
            raise FileNotFoundError(src)
        stem = src.stem
        dst = work_dir / f'{stem}.csv'
        if dst.exists() and dst.stat().st_size > 0:
            out[stem] = dst
            continue
        if not is_xlsx_disguised_as_csv(src):
            shutil.copy2(src, dst)
            out[stem] = dst
            continue

        # XLSX bytes with a misleading .csv extension.
        xlsx_copy = work_dir / f'{stem}.xlsx'
        shutil.copy2(src, xlsx_copy)
        lo = shutil.which('libreoffice') or shutil.which('soffice')
        if not lo:
            raise RuntimeError(
                f'{src.name} is an XLSX workbook despite the .csv extension. '
                'Rename/copy it to .xlsx and export sheet "Result 1" to CSV before rerunning.'
            )
        subprocess.run(
            [lo, '--headless', '--convert-to', 'csv', '--outdir', str(work_dir), str(xlsx_copy)],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        if not dst.exists():
            raise RuntimeError(f'CSV conversion failed for {src.name}')
        out[stem] = dst
    return out


def _sample_values(s: pd.Series, n: int = 5) -> str:
    vals = s.dropna().astype(str).drop_duplicates().head(n).tolist()
    return ' | '.join(vals)


def audit_csv(
    name: str,
    path: Path,
    chunksize: int = 100_000,
    sample_n: int = 50_000,
    raw_path: Path | None = None,
):
    meta = META[name]
    header = read_csv(path, nrows=0).columns.tolist()
    first = read_csv(path, nrows=sample_n, low_memory=False)

    miss = Counter({c: 0 for c in header})
    total = 0
    date_min = {c: None for c in meta['date_cols'] if c in header}
    date_max = {c: None for c in meta['date_cols'] if c in header}
    date_invalid = Counter({c: 0 for c in date_min})

    key_cols = [c for c in meta['key'] if c in header]
    seen_keys = set()
    duplicate_key_rows = 0
    missing_key_rows = 0

    for chunk in read_csv(path, chunksize=chunksize, low_memory=False):
        total += len(chunk)
        for c in header:
            miss[c] += int(chunk[c].isna().sum())

        if key_cols:
            key_frame = chunk[key_cols]
            missing_mask = key_frame.isna().any(axis=1)
            missing_key_rows += int(missing_mask.sum())
            valid = key_frame.loc[~missing_mask]
            if len(key_cols) == 1:
                keys = valid[key_cols[0]].tolist()
            else:
                keys = list(valid.itertuples(index=False, name=None))
            for k in keys:
                if k in seen_keys:
                    duplicate_key_rows += 1
                else:
                    seen_keys.add(k)

        for c in date_min:
            orig_nonnull = chunk[c].notna()
            parsed = pd.to_datetime(chunk[c], errors='coerce')
            date_invalid[c] += int((orig_nonnull & parsed.isna()).sum())
            pc = parsed.dropna()
            if not pc.empty:
                mn, mx = pc.min(), pc.max()
                date_min[c] = mn if date_min[c] is None or mn < date_min[c] else date_min[c]
                date_max[c] = mx if date_max[c] is None or mx > date_max[c] else date_max[c]

    col_rows = []
    for c in header:
        s = first[c] if c in first.columns else pd.Series(dtype='object')
        col_rows.append({
            'dataset': name,
            'column': c,
            'dtype_sample': str(s.dtype),
            'missing_count': miss[c],
            'missing_pct': miss[c] / total if total else np.nan,
            'n_unique_first_50k': int(s.nunique(dropna=True)),
            'sample_values': _sample_values(s),
            'datetime_min': date_min.get(c),
            'datetime_max': date_max.get(c),
            'invalid_datetime_count': date_invalid.get(c, np.nan),
        })

    summary = {
        'dataset': name,
        'grain': meta['grain'],
        'rows': total,
        'columns': len(header),
        'expected_key': ' + '.join(meta['key']),
        'unique_key_count': len(seen_keys) if key_cols else np.nan,
        'missing_key_rows': missing_key_rows if key_cols else np.nan,
        'duplicate_key_rows': duplicate_key_rows if key_cols else np.nan,
        'raw_file_is_xlsx_despite_csv_name': is_xlsx_disguised_as_csv(raw_path or path),
    }
    return summary, pd.DataFrame(col_rows)


def read_id_set(path: Path, col: str) -> set:
    out = set()
    for chunk in read_csv(path, usecols=[col], chunksize=200_000, low_memory=False):
        out.update(chunk[col].dropna().astype('int64').tolist())
    return out


def row_match_rate(path: Path, col: str, parent_ids: set) -> tuple[int,int,float]:
    n = m = 0
    for chunk in read_csv(path, usecols=[col], chunksize=200_000, low_memory=False):
        s = pd.to_numeric(chunk[col], errors='coerce').dropna().astype('int64')
        n += len(s)
        m += int(s.isin(parent_ids).sum())
    return m, n, (m/n if n else np.nan)


def build_join_audit(paths: dict[str, Path]) -> pd.DataFrame:
    encounter_ids = read_id_set(paths['01_encounter'], 'nb_dot_dieu_tri_id')
    exam_ids = read_id_set(paths['03_exam'], 'nb_dich_vu_id')
    service_ids = read_id_set(paths['04_service_journey'], 'nb_dich_vu_id')

    specs = [
        ('03_exam → 01_encounter', paths['03_exam'], 'nb_dot_dieu_tri_id', encounter_ids),
        ('02_reception → 01_encounter', paths['02_reception'], 'nb_dot_dieu_tri_id', encounter_ids),
        ('04_service_journey → 01_encounter', paths['04_service_journey'], 'nb_dot_dieu_tri_id', encounter_ids),
        ('05_exam_status_log → 03_exam', paths['05_exam_status_log'], 'nb_dich_vu_id', exam_ids),
        ('03_exam → 04_service_journey', paths['03_exam'], 'nb_dich_vu_id', service_ids),
    ]
    rows = []
    for rel, p, col, parent in specs:
        matched, total, rate = row_match_rate(p, col, parent)
        rows.append({
            'relationship': rel,
            'child_rows_nonnull_key': total,
            'matched_rows': matched,
            'unmatched_rows': total - matched,
            'match_rate': rate,
        })
    return pd.DataFrame(rows)


def build_target(paths: dict[str, Path]):
    status_path = paths['05_exam_status_log']
    event_parts = []
    status_counter = Counter()
    status_name_counter = Counter()
    total_status_rows = 0

    usecols = ['nb_dich_vu_id','nb_dot_dieu_tri_id','thoi_gian_thay_doi_ban_ghi','previous_status','trang_thai','status_name']
    for chunk in read_csv(status_path, usecols=usecols, chunksize=200_000, low_memory=False):
        total_status_rows += len(chunk)
        curr = pd.to_numeric(chunk['trang_thai'], errors='coerce')
        for k, v in curr.value_counts(dropna=False).items():
            status_counter[str(k)] += int(v)
        for k, v in chunk['status_name'].fillna('<NA>').value_counts().items():
            status_name_counter[str(k)] += int(v)

        keep = curr.isin([20,40,60])
        x = chunk.loc[keep, ['nb_dich_vu_id','nb_dot_dieu_tri_id','thoi_gian_thay_doi_ban_ghi','trang_thai']].copy()
        x['nb_dich_vu_id'] = pd.to_numeric(x['nb_dich_vu_id'], errors='coerce').astype('Int64')
        x['nb_dot_dieu_tri_id'] = pd.to_numeric(x['nb_dot_dieu_tri_id'], errors='coerce').astype('Int64')
        x['trang_thai'] = pd.to_numeric(x['trang_thai'], errors='coerce').astype('Int64')
        x['event_time'] = pd.to_datetime(x['thoi_gian_thay_doi_ban_ghi'], errors='coerce')
        x = x.drop(columns='thoi_gian_thay_doi_ban_ghi').dropna(subset=['nb_dich_vu_id','event_time','trang_thai'])
        event_parts.append(x)

    ev = (
        pd.concat(event_parts, ignore_index=True)
        if event_parts
        else pd.DataFrame(columns=[
            'nb_dich_vu_id', 'nb_dot_dieu_tri_id', 'trang_thai', 'event_time'
        ])
    )
    ev['nb_dich_vu_id'] = ev['nb_dich_vu_id'].astype('int64')
    ev['trang_thai'] = ev['trang_thai'].astype('int64')

    first60 = (ev.loc[ev.trang_thai.eq(60)]
                 .groupby('nb_dich_vu_id', as_index=False)['event_time'].min()
                 .rename(columns={'event_time':'exam_start_time'}))
    first20 = (ev.loc[ev.trang_thai.eq(20)]
                 .groupby('nb_dich_vu_id', as_index=False)['event_time'].min()
                 .rename(columns={'event_time':'first_status20_time'}))
    any40 = set(ev.loc[ev.trang_thai.eq(40),'nb_dich_vu_id'].unique().tolist())
    any60 = set(first60['nb_dich_vu_id'].tolist())
    any20 = set(first20['nb_dich_vu_id'].tolist())

    s20 = ev.loc[ev.trang_thai.eq(20), ['nb_dich_vu_id','event_time']].merge(first60, on='nb_dich_vu_id', how='inner')
    s20 = s20.loc[s20['event_time'] <= s20['exam_start_time']]
    latest20 = (s20.groupby('nb_dich_vu_id', as_index=False)['event_time'].max()
                   .rename(columns={'event_time':'latest_status20_before60_time'}))

    s40 = ev.loc[ev.trang_thai.eq(40), ['nb_dich_vu_id','event_time']].merge(first60, on='nb_dich_vu_id', how='inner')
    s40 = s40.loc[s40['event_time'] <= s40['exam_start_time']]
    latest40 = (s40.groupby('nb_dich_vu_id', as_index=False)['event_time'].max()
                   .rename(columns={'event_time':'latest_status40_before60_time'}))

    exam_cols = [
        'nb_dich_vu_id','nb_dot_dieu_tri_id','dich_vu_id','ma_dich_vu','ten_dich_vu',
        'thoi_gian_chi_dinh','stt','uu_tien','cap_cuu','phong_thuc_hien_id',
        'ma_phong_thuc_hien','ten_phong_thuc_hien','khoa_thuc_hien_id',
        'ma_khoa_thuc_hien','ten_khoa_thuc_hien','bac_si_kham_id','ma_bac_si_kham'
    ]
    exam = read_csv(paths['03_exam'], usecols=exam_cols, low_memory=False)
    exam['nb_dich_vu_id'] = pd.to_numeric(exam['nb_dich_vu_id'], errors='coerce').astype('Int64')
    exam = exam.dropna(subset=['nb_dich_vu_id']).copy()
    exam['nb_dich_vu_id'] = exam['nb_dich_vu_id'].astype('int64')
    if exam['nb_dich_vu_id'].duplicated().any():
        exam = exam.drop_duplicates('nb_dich_vu_id', keep='first')

    target = exam.merge(first60, on='nb_dich_vu_id', how='left')
    target = target.merge(first20, on='nb_dich_vu_id', how='left')
    target = target.merge(latest20, on='nb_dich_vu_id', how='left')
    target = target.merge(latest40, on='nb_dich_vu_id', how='left')
    target['has_status_20_any'] = target['nb_dich_vu_id'].isin(any20)
    target['has_status_40_any'] = target['nb_dich_vu_id'].isin(any40)
    target['has_status_60_any'] = target['nb_dich_vu_id'].isin(any60)
    target['has_status_20_before_60'] = target['latest_status20_before60_time'].notna()
    target['has_status_40_before_60'] = target['latest_status40_before60_time'].notna()

    service_code = target['ma_dich_vu'].astype('string').str.strip().str.upper()
    room_code = target['ma_phong_thuc_hien'].astype('string').str.strip().str.upper()
    target['uses_status20_target'] = service_code.eq('KB008') | room_code.eq('H401')
    target['queue_entry_status'] = np.where(target['uses_status20_target'], 20, 40)
    target['target_rule'] = np.where(
        target['uses_status20_target'], 'status_20_to_60', 'status_40_to_60'
    )
    target['queue_entry_time'] = target['latest_status40_before60_time']
    target.loc[target['uses_status20_target'], 'queue_entry_time'] = target.loc[
        target['uses_status20_target'], 'latest_status20_before60_time'
    ]
    target['required_queue_status_present'] = target['queue_entry_time'].notna()
    target['waiting_minutes'] = (target['exam_start_time'] - target['queue_entry_time']).dt.total_seconds() / 60.0
    target['target_valid_v2'] = target['waiting_minutes'].notna() & target['waiting_minutes'].ge(0)
    target['cross_day_wait'] = (
        target['target_valid_v2'] &
        target['queue_entry_time'].dt.date.ne(target['exam_start_time'].dt.date)
    )
    target['wait_gt_2h'] = target['waiting_minutes'].gt(120)
    target['wait_gt_4h'] = target['waiting_minutes'].gt(240)
    target['wait_gt_24h'] = target['waiting_minutes'].gt(1440)

    conditions = [
        ~target['has_status_60_any'],
        target['has_status_60_any'] & target['uses_status20_target'] & ~target['has_status_20_before_60'],
        target['has_status_60_any'] & ~target['uses_status20_target'] & ~target['has_status_40_before_60'],
        target['waiting_minutes'].lt(0),
    ]
    choices = ['missing_status_60', 'missing_required_status_20', 'missing_required_status_40', 'negative_wait']
    target['target_issue'] = np.select(conditions, choices, default='valid_v2')

    status_dist = pd.DataFrame([
        {'status_code': k, 'count': v, 'pct_rows': v/total_status_rows if total_status_rows else np.nan}
        for k,v in status_counter.items()
    ]).sort_values('count', ascending=False)
    status_name_dist = pd.DataFrame([
        {'status_name': k, 'count': v, 'pct_rows': v/total_status_rows if total_status_rows else np.nan}
        for k,v in status_name_counter.items()
    ]).sort_values('count', ascending=False)

    return target, status_dist, status_name_dist


def target_validation_tables(target: pd.DataFrame):
    valid = target.loc[target['target_valid_v2'], 'waiting_minutes']
    q = valid.quantile([0, .01, .05, .25, .5, .75, .9, .95, .99, 1]).reset_index()
    q.columns = ['quantile','waiting_minutes']

    total = len(target)
    n_valid = int(target['target_valid_v2'].sum())
    summary = pd.DataFrame([
        ('exam_services', total, total/total if total else np.nan),
        ('has_status_20_any', int(target['has_status_20_any'].sum()), float(target['has_status_20_any'].mean())),
        ('has_status_40_any', int(target['has_status_40_any'].sum()), float(target['has_status_40_any'].mean())),
        ('has_status_60_any', int(target['has_status_60_any'].sum()), float(target['has_status_60_any'].mean())),
        ('has_status_40_before_60', int(target['has_status_40_before_60'].sum()), float(target['has_status_40_before_60'].mean())),
        ('uses_status20_target', int(target['uses_status20_target'].sum()), float(target['uses_status20_target'].mean())),
        ('valid_target_v2', n_valid, n_valid/total if total else np.nan),
        ('waiting_eq_0', int(valid.eq(0).sum()), float(valid.eq(0).mean()) if n_valid else np.nan),
        ('waiting_gt_2h', int(valid.gt(120).sum()), float(valid.gt(120).mean()) if n_valid else np.nan),
        ('waiting_gt_4h', int(valid.gt(240).sum()), float(valid.gt(240).mean()) if n_valid else np.nan),
        ('waiting_gt_24h', int(valid.gt(1440).sum()), float(valid.gt(1440).mean()) if n_valid else np.nan),
        ('cross_day_wait', int(target.loc[target.target_valid_v2,'cross_day_wait'].sum()), float(target.loc[target.target_valid_v2,'cross_day_wait'].mean()) if n_valid else np.nan),
        ('missing_required_queue_status', int((target.has_status_60_any & ~target.required_queue_status_present).sum()), np.nan),
    ], columns=['metric','count','rate'])

    by_room = (target.assign(missing_target=~target['target_valid_v2'])
        .groupby(['phong_thuc_hien_id','ma_phong_thuc_hien','ten_phong_thuc_hien'], dropna=False)
        .agg(exam_count=('nb_dich_vu_id','size'), valid_target=('target_valid_v2','sum'), missing_target=('missing_target','sum'))
        .reset_index())
    by_room['missing_rate'] = by_room['missing_target']/by_room['exam_count']
    by_room = by_room.sort_values(['missing_rate','exam_count'], ascending=[False,False])

    by_doctor = (target.assign(missing_target=~target['target_valid_v2'])
        .groupby(['bac_si_kham_id','ma_bac_si_kham'], dropna=False)
        .agg(exam_count=('nb_dich_vu_id','size'), valid_target=('target_valid_v2','sum'), missing_target=('missing_target','sum'))
        .reset_index())
    by_doctor['missing_rate'] = by_doctor['missing_target']/by_doctor['exam_count']
    by_doctor = by_doctor.sort_values(['missing_rate','exam_count'], ascending=[False,False])

    issue = target['target_issue'].value_counts(dropna=False).rename_axis('target_issue').reset_index(name='count')
    issue['rate'] = issue['count']/len(target) if len(target) else np.nan
    return summary, q, issue, by_room, by_doctor



def build_business_checks(paths: dict[str, Path], target: pd.DataFrame) -> tuple[pd.DataFrame,pd.DataFrame,pd.DataFrame]:
    rows = []

    enc = read_csv(paths['01_encounter'], usecols=['thoi_gian_vao_vien','thoi_gian_ra_vien','arrival_hour'], low_memory=False)
    enc_in = pd.to_datetime(enc['thoi_gian_vao_vien'], errors='coerce')
    rows.append({'check':'Encounter: missing thoi_gian_ra_vien','value':int(enc['thoi_gian_ra_vien'].isna().sum()),'rate':float(enc['thoi_gian_ra_vien'].isna().mean()),'interpretation':'Không dùng tổng LOS làm target duy nhất nếu chưa xử lý missing.'})
    rows.append({'check':'Encounter: invalid thoi_gian_vao_vien','value':int(enc_in.isna().sum()),'rate':float(enc_in.isna().mean()),'interpretation':'Timestamp vào viện cần hợp lệ cho feature thời gian.'})
    arrival_hour_dist = enc['arrival_hour'].value_counts(dropna=False).rename_axis('arrival_hour').reset_index(name='count')
    arrival_hour_dist['rate'] = arrival_hour_dist['count']/len(enc) if len(enc) else np.nan
    arrival_hour_dist = arrival_hour_dist.sort_values('arrival_hour')

    rec = read_csv(paths['02_reception'], usecols=['vao_vien_den_lay_so_phut','lay_so_den_hoan_thanh_phut'], low_memory=False)
    for c in rec.columns:
        x = pd.to_numeric(rec[c], errors='coerce')
        nonnull = x.dropna()
        rows.append({'check':f'Reception: {c} = 0','value':int(nonnull.eq(0).sum()),'rate':float(nonnull.eq(0).mean()) if len(nonnull) else np.nan,'interpretation':'Tỷ lệ 0 rất cao làm hạn chế giá trị của timestamp tiếp đón cho target waiting.'})

    exam = read_csv(paths['03_exam'], usecols=['thoi_gian_tiep_nhan','thoi_gian_thuc_hien','thoi_gian_thuc_hien_trung_binh','bac_si_kham_id'], low_memory=False)
    a = pd.to_datetime(exam['thoi_gian_tiep_nhan'], errors='coerce')
    b = pd.to_datetime(exam['thoi_gian_thuc_hien'], errors='coerce')
    both = a.notna() & b.notna()
    eq = (a[both] == b[both])
    rows.append({'check':'Exam: thoi_gian_tiep_nhan = thoi_gian_thuc_hien (among both non-null)','value':int(eq.sum()),'rate':float(eq.mean()) if len(eq) else np.nan,'interpretation':'Không phù hợp để lấy chênh lệch hai trường này làm waiting target.'})
    rows.append({'check':'Exam: missing thoi_gian_thuc_hien_trung_binh','value':int(exam['thoi_gian_thuc_hien_trung_binh'].isna().sum()),'rate':float(exam['thoi_gian_thuc_hien_trung_binh'].isna().mean()),'interpretation':'Không dùng trực tiếp trường này; có thể tự tính rolling average từ lịch sử.'})
    rows.append({'check':'Exam: missing bac_si_kham_id','value':int(exam['bac_si_kham_id'].isna().sum()),'rate':float(exam['bac_si_kham_id'].isna().mean()),'interpretation':'Cần xử lý missing/unknown doctor khi modeling.'})

    cnt = Counter(); total_service = 0
    for ch in read_csv(paths['04_service_journey'], usecols=['loai_dich_vu'], chunksize=200_000, low_memory=False):
        s = pd.to_numeric(ch['loai_dich_vu'], errors='coerce')
        cnt.update(s.dropna().astype('int64').tolist()); total_service += len(ch)
    service_type_dist = pd.DataFrame([
        {
            'loai_dich_vu': k,
            'count': v,
            'rate': v / total_service if total_service else np.nan,
        }
        for k, v in sorted(cnt.items())
    ])

    valid = target.loc[target['target_valid_v2']]
    rows.append({'check':'Target V2: valid theo quy tắc nghiệp vụ','value':int(len(valid)),'rate':float(len(valid)/len(target)) if len(target) else np.nan,'interpretation':'KB008 hoặc phòng H401 dùng 20 -> 60; các ca còn lại dùng 40 -> 60.'})
    rows.append({'check':'Target V2: waiting > 24h','value':int(valid['waiting_minutes'].gt(1440).sum()),'rate':float(valid['waiting_minutes'].gt(1440).mean()),'interpretation':'Trường hợp thực tế; giữ nguyên trong dữ liệu và gắn cờ để báo cáo.'})
    return pd.DataFrame(rows), arrival_hour_dist, service_type_dist

def _safe_sheet_name(name: str) -> str:
    return name[:31]


def _write_df(ws, df: pd.DataFrame):
    for j, col in enumerate(df.columns, 1):
        ws.cell(1,j,col)
    for i, row in enumerate(df.itertuples(index=False, name=None), 2):
        for j, val in enumerate(row,1):
            if pd.isna(val):
                val = None
            elif isinstance(val, pd.Timestamp):
                val = val.to_pydatetime()
            elif isinstance(val, np.generic):
                val = val.item()
            ws.cell(i,j,val)


def style_workbook(path: Path):
    wb = load_workbook(path)
    header_fill = PatternFill('solid', fgColor='1F4E78')
    header_font = Font(color='FFFFFF', bold=True)
    for ws in wb.worksheets:
        ws.freeze_panes = 'A2'
        ws.sheet_view.showGridLines = False
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        ws.auto_filter.ref = ws.dimensions
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical='top', wrap_text=True)
        for col_idx, col_cells in enumerate(ws.columns, 1):
            max_len = 0
            for cell in list(col_cells)[:300]:
                if cell.value is not None:
                    max_len = max(max_len, len(str(cell.value)))
            width = min(max(max_len + 2, 10), 42)
            ws.column_dimensions[get_column_letter(col_idx)].width = width
    # Explicit percent formats by header text.
    for ws in wb.worksheets:
        headers = {c.value: c.column for c in ws[1]}
        for h, idx in headers.items():
            if h and (str(h).endswith('_pct') or str(h).endswith('_rate') or str(h) in {'rate','match_rate','pct_rows','missing_pct','missing_rate'}):
                for r in range(2, ws.max_row+1):
                    ws.cell(r, idx).number_format='0.0%'
        # Datetime columns
        for h, idx in headers.items():
            if h and ('time' in str(h).lower() or str(h).endswith('_min') or str(h).endswith('_max')):
                for r in range(2, min(ws.max_row, 5000)+1):
                    if hasattr(ws.cell(r,idx).value, 'year'):
                        ws.cell(r,idx).number_format='yyyy-mm-dd hh:mm:ss'
    wb.save(path)


def write_report(out_path: Path, tables: dict[str, pd.DataFrame], readme_rows: list[tuple[str,str]]):
    wb = Workbook()
    ws = wb.active
    ws.title = 'README'
    readme_df = pd.DataFrame(readme_rows, columns=['Mục','Nội dung'])
    _write_df(ws, readme_df)
    for name, df in tables.items():
        ws = wb.create_sheet(_safe_sheet_name(name))
        _write_df(ws, df)
    wb.save(out_path)
    style_workbook(out_path)


def main(
    raw_dir: str | Path = '01_data_input',
    normalized_dir: str | Path = '03_work/csv',
    out_dir: str | Path = '03_output',
):
    raw_dir = Path(raw_dir)
    normalized_dir = Path(normalized_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Use already normalized intermediate CSVs when present; otherwise normalize raw files.
    paths = {}
    all_present = all((normalized_dir / n).exists() for n in RAW_NAMES)
    if all_present:
        paths = {Path(n).stem: normalized_dir / n for n in RAW_NAMES}
    else:
        paths = normalize_inputs(raw_dir, normalized_dir)

    summaries = []
    col_tables = []
    for name in [Path(n).stem for n in RAW_NAMES]:
        s, c = audit_csv(name, paths[name], raw_path=raw_dir / f'{name}.csv')
        summaries.append(s)
        col_tables.append(c)
        print(f'Audited {name}: {s["rows"]:,} rows')
    dataset_summary = pd.DataFrame(summaries)
    column_quality = pd.concat(col_tables, ignore_index=True)

    join_audit = build_join_audit(paths)
    print('Join audit complete')

    target, status_dist, status_name_dist = build_target(paths)
    print(f'Target table built: {len(target):,} exam services')
    target_summary, quantiles, issue_dist, by_room, by_doctor = target_validation_tables(target)
    business_checks, arrival_hour_dist, service_type_dist = build_business_checks(paths, target)
    top_wait_outliers = (target.loc[target['target_valid_v2']]
        .sort_values('waiting_minutes', ascending=False)
        .head(100)[['nb_dich_vu_id','nb_dot_dieu_tri_id','target_rule','queue_entry_status','queue_entry_time','exam_start_time','waiting_minutes','phong_thuc_hien_id','ma_phong_thuc_hien','bac_si_kham_id','ma_bac_si_kham','cross_day_wait']])

    target_path = out_dir / 'exam_wait_target_v2.csv'
    target.to_csv(target_path, index=False, encoding='utf-8-sig', date_format='%Y-%m-%d %H:%M:%S.%f')

    valid_path = out_dir / 'exam_wait_target_v2_valid.csv'
    target.loc[target['target_valid_v2']].to_csv(valid_path, index=False, encoding='utf-8-sig', date_format='%Y-%m-%d %H:%M:%S.%f')

    report_path = out_dir / 'HIS_data_audit_report.xlsx'
    readme_rows = [
        ('Mục tiêu', 'Audit 5 nguồn HIS và xác minh target waiting_minutes trước EDA/ML.'),
        ('Target V2', 'KB008 hoặc phòng H401: status 20 cuối trước status 60 đầu; các ca khác: status 40 cuối trước status 60 đầu.'),
        ('Quy tắc nghiệp vụ', 'KB008 và phòng H401 không cần đo thị lực nên bắt đầu chờ từ status 20; các ca còn lại bắt đầu chờ sau đo thị lực ở status 40.'),
        ('Đơn vị target', '1 dòng / nb_dich_vu_id (dịch vụ khám).'),
        ('Quy tắc valid', 'Có status bắt buộc theo đúng nhánh nghiệp vụ trước status 60 và waiting_minutes >= 0.'),
        ('Outlier', 'Các ca >2h, >4h, >24h và cross-day là trường hợp thực tế; giữ nguyên và gắn cờ để báo cáo.'),
        ('Định dạng nguồn', '5 file gốc mang tên .csv nhưng bytes là workbook XLSX; pipeline chuẩn hóa thành CSV trung gian trước phân tích.'),
    ]
    tables = {
        'dataset_summary': dataset_summary,
        'column_quality': column_quality,
        'join_quality': join_audit,
        'status_code_dist': status_dist,
        'status_name_dist': status_name_dist,
        'target_summary': target_summary,
        'waiting_quantiles': quantiles,
        'target_issue_dist': issue_dist,
        'business_checks': business_checks,
        'arrival_hour_dist': arrival_hour_dist,
        'service_type_dist': service_type_dist,
        'top_wait_outliers': top_wait_outliers,
        'missing_by_room': by_room,
        'missing_by_doctor': by_doctor,
    }
    write_report(report_path, tables, readme_rows)

    # Machine-readable summary for notebook/reporting.
    valid = target.loc[target.target_valid_v2, 'waiting_minutes']
    summary_json = {
        'dataset_rows': {r['dataset']: int(r['rows']) for r in summaries},
        'target': {
            'exam_services': int(len(target)),
            'has_status_60': int(target.has_status_60_any.sum()),
            'has_status_20_before_60': int(target.has_status_20_before_60.sum()),
            'has_status_40_before_60': int(target.has_status_40_before_60.sum()),
            'uses_status20_target': int(target.uses_status20_target.sum()),
            'uses_status40_target': int((~target.uses_status20_target).sum()),
            'valid_target_v2': int(target.target_valid_v2.sum()),
            'valid_rate': float(target.target_valid_v2.mean()),
            'median_waiting_minutes': float(valid.median()) if len(valid) else None,
            'p90_waiting_minutes': float(valid.quantile(.90)) if len(valid) else None,
            'p95_waiting_minutes': float(valid.quantile(.95)) if len(valid) else None,
            'p99_waiting_minutes': float(valid.quantile(.99)) if len(valid) else None,
            'waiting_gt_2h': int(valid.gt(120).sum()),
            'waiting_gt_4h': int(valid.gt(240).sum()),
            'waiting_gt_24h': int(valid.gt(1440).sum()),
            'cross_day_wait': int(target.loc[target.target_valid_v2,'cross_day_wait'].sum()),
            'missing_required_status_20': int((target.has_status_60_any & target.uses_status20_target & ~target.has_status_20_before_60).sum()),
            'missing_required_status_40': int((target.has_status_60_any & ~target.uses_status20_target & ~target.has_status_40_before_60).sum()),
        },
        'joins': join_audit.to_dict(orient='records'),
    }
    with open(out_dir / 'step1_summary.json', 'w', encoding='utf-8') as f:
        json.dump(summary_json, f, ensure_ascii=False, indent=2, default=str)

    return summary_json


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Audit HIS extracts and build waiting-time target.')
    parser.add_argument('--raw-dir', type=Path, default=Path('01_data_input'))
    parser.add_argument('--normalized-dir', type=Path, default=Path('03_work/csv'))
    parser.add_argument('--out-dir', type=Path, default=Path('03_output'))
    args = parser.parse_args()
    result = main(args.raw_dir, args.normalized_dir, args.out_dir)
    # ASCII escapes keep CLI output portable on Windows consoles using cp1252.
    print(json.dumps(result, ensure_ascii=True, indent=2, default=str))
