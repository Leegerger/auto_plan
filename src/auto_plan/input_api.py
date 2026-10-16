from typing import Any
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from .app import require_editor, require_viewer
from .access import StoreUnavailable
from .inputs import InputConflict, InputErrors, apply_batch, preview, continue_months, selection_options, availability
from .input_models import InputSnapshot, month_value, date_value

router = APIRouter()


def input_store(request: Request):
    if request.app.state.input_store is None:
        pool = getattr(request.app.state.access_store, 'pool', None)
        if pool is None:
            raise StoreUnavailable
        from .oracle_inputs import OracleInputStore
        request.app.state.input_store = OracleInputStore(pool)
    return request.app.state.input_store


class Batch(BaseModel):
    base_version: int = Field(ge=0, strict=True)
    changes: dict[str, list[dict[str, Any]]]


class InputPreviewError(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    sheet: str
    row: int
    field: str
    reason: str


class InputPreview(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    valid: bool
    base_version: int
    errors: list[InputPreviewError]
    changes: dict[str, list[dict[str, JsonValue]]]
    snapshot: InputSnapshot | None


@router.get('/inputs')
def read_inputs(request: Request, version: int | None = None, principal=Depends(require_viewer)) -> InputSnapshot:
    try:
        if version is None:
            now = request.app.state.clock()
            month = now.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m')
            return input_store(request).update('SYSTEM_MONTH_CONTINUATION', now, lambda current: continue_months(current, month))
        return input_store(request).read(version)
    except KeyError:
        raise HTTPException(404, '输入版本不存在') from None


@router.get('/inputs/options')
def options(request: Request, principal=Depends(require_viewer)):
    return selection_options(input_store(request).read())


@router.get('/inputs/availability')
def get_availability(request: Request, month: str, model_code: str, line_code: str, date: str | None = None, from_recipe: str | None = None, principal=Depends(require_viewer)):
    try:
        month_value(month)
        if date is not None:
            production_date = date_value(date)
            if production_date.strftime('%Y-%m') != month:
                raise ValueError('月份必须与生产日期的投入月份一致')
        return availability(input_store(request).read(), month, model_code, line_code, date, from_recipe)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except KeyError:
        raise HTTPException(422, '主资料编码不存在') from None


@router.post('/inputs/preview')
def preview_inputs(payload: Batch, request: Request, principal=Depends(require_editor)) -> InputPreview:
    try:
        return preview(input_store(request).read(), payload.base_version, payload.changes)
    except InputConflict:
        raise HTTPException(409, '资料已更新，请重新读取差异并预览') from None


@router.post('/inputs/apply')
def apply_inputs(payload: Batch, request: Request, principal=Depends(require_editor)) -> InputSnapshot:
    try:
        return input_store(request).update(principal.user_id, request.app.state.clock(),
            lambda current: apply_batch(current, payload.base_version, payload.changes))
    except InputConflict:
        raise HTTPException(409, '资料已更新，请重新读取差异并预览') from None
    except InputErrors as exc:
        raise HTTPException(422, {'message': '整批未提交，请修正错误', 'errors': exc.errors}) from None


class Paste(BaseModel):
    base_version: int = Field(ge=0, strict=True)
    kind: str
    text: str = Field(max_length=10 * 1024 * 1024)


def adapted_preview(request, base_version, changes, adapter_errors):
    try:
        result = preview(input_store(request).read(), base_version, changes)
    except InputConflict:
        raise HTTPException(409, '资料已更新，请重新读取差异并预览') from None
    result['errors'] = adapter_errors + result['errors']
    result['valid'] = not result['errors']
    if not result['valid']:
        result['snapshot'] = None
    return result


@router.post('/inputs/paste/preview')
def preview_paste(payload: Paste, request: Request, principal=Depends(require_editor)):
    from .input_tables import paste_rows
    changes, errors = paste_rows(payload.kind, payload.text)
    return adapted_preview(request, payload.base_version, changes, errors)


@router.post('/inputs/excel/preview')
async def preview_excel(request: Request, base_version: int, principal=Depends(require_editor)):
    from .input_tables import excel_rows, MAX_UPLOAD
    from starlette.concurrency import run_in_threadpool
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_UPLOAD:
            raise HTTPException(413, '工作簿最大10MB')
    changes, errors = await run_in_threadpool(excel_rows, bytes(content))
    return await run_in_threadpool(adapted_preview, request, base_version, changes, errors)


@router.get('/inputs/template.xlsx')
def template(principal=Depends(require_viewer)):
    from fastapi.responses import Response
    from .input_tables import workbook_bytes
    return Response(workbook_bytes(), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition': 'attachment; filename="auto-plan-input-template.xlsx"'})


@router.get('/inputs/export.xlsx')
def export_inputs(request: Request, version: int | None = None, principal=Depends(require_viewer)):
    from fastapi.responses import Response
    from .input_tables import workbook_bytes
    try:
        snapshot = input_store(request).read(version)
    except KeyError:
        raise HTTPException(404, '输入版本不存在') from None
    return Response(workbook_bytes(snapshot), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition': f'attachment; filename="auto-plan-input-v{snapshot.input_version}.xlsx"'})
