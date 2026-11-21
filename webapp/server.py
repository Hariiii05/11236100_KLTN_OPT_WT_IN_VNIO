"""FastAPI application: JSON API + static single-page frontend."""

from __future__ import annotations

import time
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from webapp.config import CONFIG
from webapp.dashboard import Dashboard
from webapp.history import load_history
from webapp.model_service import WaitTimeModel
from webapp.operations import Operations, ValidationError
from webapp.store import Store


class ModelInputs(BaseModel):
    ma_dich_vu: str | None = None
    ma_phong_thuc_hien: str | None = None
    ma_khoa: str | None = None
    patient_payment_type: str | None = None
    uu_tien: bool | None = None
    cap_cuu: bool | None = None
    ordered_time: str | None = None


class ServiceChoice(BaseModel):
    ma_dich_vu: str | None = None
    ma_phong_thuc_hien: str | None = None
    bac_si: str | None = None
    ordered_time: str | None = None


class Registration(BaseModel):
    admin: dict[str, Any]
    service: ServiceChoice


class ClockUpdate(BaseModel):
    now: str | None = None
    speed: float | None = None
    paused: bool | None = None


def create_app() -> FastAPI:
    started = time.time()
    print('[webapp] loading model bundle and replay data (first run builds a cache)...', flush=True)
    model = WaitTimeModel(CONFIG.model_path)
    history = load_history(CONFIG)
    store = Store(CONFIG.db_path)
    ops = Operations(CONFIG, history, model, store)
    dashboard = Dashboard(CONFIG, history, ops)
    holdout_summary = {k: v for k, v in dashboard.forecast({}, 30).items() if not isinstance(v, list)}
    print(f'[webapp] ready in {time.time() - started:.1f}s - model {model.path.name}, '
          f'{len(history.cohort):,} replay rows', flush=True)

    app = FastAPI(title='Hệ thống dự báo thời gian chờ khám', version='1.0',
                  description='API tiếp đón, dự báo thời gian chờ (mô hình V3), hàng đợi QMS và dashboard.')

    @app.exception_handler(ValidationError)
    async def _validation(_, exc: ValidationError):  # noqa: ANN001
        return JSONResponse(status_code=422, content={'detail': 'Thiếu hoặc sai dữ liệu', 'errors': exc.errors})

    @app.exception_handler(KeyError)
    async def _missing(_, exc: KeyError):  # noqa: ANN001
        return JSONResponse(status_code=404, content={'detail': f'Không tìm thấy {exc.args[0]}'})

    # -------------------------------------------------------------- meta
    @app.get('/api/meta')
    def meta() -> dict[str, Any]:
        return {'hospital': CONFIG.hospital_name, 'model': model.metadata(), 'clock': ops.clock.state(),
                'catalog': ops.catalog(),
                'dashboard': dashboard.options(), 'holdout': holdout_summary,
                'thresholds': {'wait': CONFIG.alert_wait_minutes, 'queue': CONFIG.alert_queue_length}}

    @app.get('/api/clock')
    def clock() -> dict[str, Any]:
        return ops.clock.state()

    @app.post('/api/clock')
    def set_clock(update: ClockUpdate) -> dict[str, Any]:
        try:
            ops.clock.update(update.now, update.speed, update.paused)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return ops.clock.state()

    # --------------------------------------------------------- reception
    @app.post('/api/rooms/options')
    def room_options(inputs: ModelInputs) -> dict[str, Any]:
        return ops.room_options(inputs.model_dump())

    @app.post('/api/predict')
    def predict(inputs: ModelInputs) -> dict[str, Any]:
        return ops.predict(inputs.model_dump())

    @app.post('/api/reception')
    def register(payload: Registration) -> dict[str, Any]:
        return ops.register(payload.admin, payload.service.model_dump())

    @app.get('/api/patients')
    def patients(q: str = Query('', min_length=0)) -> list[dict[str, Any]]:
        return ops.search_patients(q) if len(q.strip()) >= 2 else []

    # --------------------------------------------------------------- QMS
    @app.get('/api/queue/room/{room}')
    def room_board(room: str) -> dict[str, Any]:
        return ops.room_board(room)

    @app.get('/api/queue/area')
    def area_board(department: str | None = None) -> dict[str, Any]:
        return ops.area_board(department or None)

    @app.get('/api/tickets/{ticket_id}')
    def ticket(ticket_id: str) -> dict[str, Any]:
        return ops.ticket(ticket_id)

    @app.post('/api/tickets/{ticket_id}/{action}')
    def act(ticket_id: str, action: str) -> dict[str, Any]:
        return ops.act(ticket_id, action)

    @app.post('/api/queue/room/{room}/call-next')
    def call_next(room: str) -> dict[str, Any]:
        return ops.call_next(room)

    @app.get('/api/lookup')
    def lookup(q: str) -> list[dict[str, Any]]:
        return ops.lookup(q)

    # ---------------------------------------------------------- dashboard
    def _filters(start, end, department, room, service, doctor, payment, shift, threshold, tolerance):  # noqa: ANN001
        return {'start': start, 'end': end, 'department': department, 'room': room, 'service': service,
                'doctor': doctor, 'payment': payment, 'shift': shift, 'threshold': threshold,
                'tolerance': tolerance}

    @app.get('/api/dashboard')
    def dashboard_summary(start: str | None = None, end: str | None = None, department: str | None = None,
                          room: str | None = None, service: str | None = None, doctor: str | None = None,
                          payment: str | None = None, shift: str | None = None, threshold: float = 30,
                          tolerance: float = 10) -> dict[str, Any]:
        return dashboard.summary(_filters(start, end, department, room, service, doctor, payment, shift,
                                          threshold, tolerance))

    @app.get('/api/dashboard/live')
    def dashboard_live(department: str | None = None, room: str | None = None) -> dict[str, Any]:
        return dashboard.live({'department': department, 'room': room})

    @app.post('/api/demo/reset')
    def reset(confirm: bool = Body(False, embed=True)) -> dict[str, Any]:
        if not confirm:
            raise HTTPException(status_code=400, detail='Cần xác nhận để xóa dữ liệu tiếp đón trên hệ thống.')
        store.reset_operations()
        return {'ok': True}

    # ------------------------------------------------------------ frontend
    @app.middleware('http')
    async def _no_stale_assets(request, call_next):  # noqa: ANN001
        response = await call_next(request)
        if request.url.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'no-cache'  # revalidate so UI updates show up immediately
        return response

    app.mount('/static', StaticFiles(directory=CONFIG.static_dir), name='static')

    @app.get('/', include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(CONFIG.static_dir / 'index.html', headers={'Cache-Control': 'no-cache'})

    return app
