"""Canonical maintained input types. Decimal values are lossless decimal strings."""
import re
from decimal import Decimal
from datetime import date
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, BeforeValidator, field_validator


def code_value(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 64 or any(ord(c) < 32 for c in value):
        raise ValueError('编码必填，最多64字且不能包含控制字符')
    return value.strip()


def name_value(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200:
        raise ValueError('名称必填，最多200字')
    return value.strip()


Code = Annotated[str, BeforeValidator(code_value)]
Name = Annotated[str, BeforeValidator(name_value)]


def integer_value(value):
    if isinstance(value, str) and re.fullmatch(r'\d+', value.strip()):
        value = int(value.strip())
    if type(value) is not int or value < 0:
        raise ValueError('必须填写非负整数，不能使用小数或布尔值')
    return value


def decimal_value(value, percent=False):
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r'\d+(?:\.\d{1,2})?', value.strip()):
        raise ValueError('必须填写非负十进制数，最多两位小数')
    number = Decimal(value.strip())
    if percent and not Decimal(0) < number <= Decimal(100):
        raise ValueError('百分数必须大于0且不超过100')
    return format(number, 'f')


def month_value(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}', value):
        raise ValueError('月份必须为 YYYY-MM')
    try:
        date.fromisoformat(value + '-01')
    except ValueError:
        raise ValueError('月份无效') from None
    return value


def date_value(value):
    if type(value) is date:
        return value
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('生产日期必须为 YYYY-MM-DD')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError('生产日期无效') from None


def boolean_value(value):
    if isinstance(value, str) and value.strip().lower() in {'true', 'false'}:
        return value.strip().lower() == 'true'
    if type(value) is not bool:
        raise ValueError('必须填写 true 或 false')
    return value


Pieces = Annotated[int, BeforeValidator(integer_value)]
DecimalString = Annotated[str, BeforeValidator(decimal_value)]
PercentString = Annotated[str, BeforeValidator(lambda value: decimal_value(value, percent=True))]
Month = Annotated[str, BeforeValidator(month_value)]
ProductionDate = Annotated[date, BeforeValidator(date_value)]
Boolean = Annotated[bool, BeforeValidator(boolean_value)]


class InputRow(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class Master(InputRow):
    code: Code
    name: Name
    active: Boolean


class Model(Master):
    recipe_code: Code


class Capability(InputRow):
    recipe_code: Code
    line_code: Code
    allowed: Boolean


class Masters(InputRow):
    models: list[Model] = Field(default_factory=list)
    recipes: list[Master] = Field(default_factory=list)
    lines: list[Master] = Field(default_factory=list)
    capabilities: list[Capability] = Field(default_factory=list)


class LegacyLineYield(InputRow):
    month: Month
    percent: PercentString


class LineYield(InputRow):
    month: Month
    model_code: Code
    percent: PercentString


class ModelYield(InputRow):
    month: Month
    model_code: Code
    percent: PercentString


class Capacity(InputRow):
    month: Month
    recipe_code: Code
    line_code: Code
    pieces: Pieces


class CycleTime(InputRow):
    model_code: Code
    days: DecimalString


class SwitchDuration(InputRow):
    from_recipe: Code
    to_recipe: Code
    hours: DecimalString


class Parameters(InputRow):
    line_yields: list[LineYield | LegacyLineYield] = Field(default_factory=list)
    model_yields: list[ModelYield] = Field(default_factory=list)
    capacities: list[Capacity] = Field(default_factory=list)
    cycle_times: list[CycleTime] = Field(default_factory=list)
    switch_durations: list[SwitchDuration] = Field(default_factory=list)


class Holiday(InputRow):
    date: ProductionDate
    holiday: Boolean


class OpeningException(InputRow):
    date: ProductionDate
    line_code: Code
    enabled: Boolean


class Occupancy(InputRow):
    date: ProductionDate
    line_code: Code
    hours: Pieces


class Calendar(InputRow):
    holidays: list[Holiday] = Field(default_factory=list)
    opening_exceptions: list[OpeningException] = Field(default_factory=list)
    maintenance: list[Occupancy] = Field(default_factory=list)
    experiments: list[Occupancy] = Field(default_factory=list)


class WeeklyDemand(InputRow):
    model_code: Code
    week: ProductionDate
    quantity: Pieces

    @field_validator('week')
    @classmethod
    def monday(cls, value):
        if value.weekday() != 0:
            raise ValueError('需求周必须填写周一生产日期')
        return value


class ConfirmationBatch(InputRow):
    model_code: Code
    week: ProductionDate
    kind: Literal['material', 'shipping']
    batch_id: Code
    status: Literal['allowed', 'pending', 'forbidden', 'revoked']
    date: ProductionDate | None = Field(default=None, validate_default=True)
    quantity: Pieces | None = Field(default=None, validate_default=True)
    note: str = Field(default='', max_length=1000)

    @field_validator('date', 'quantity', mode='before')
    @classmethod
    def allowed_limits(cls, value, info):
        if value is None or value == '':
            if info.data.get('status') == 'allowed':
                raise ValueError('允许批次必须填写日期限制和本批非负整数数量')
            return None
        return value

    @field_validator('note', mode='before')
    @classmethod
    def blank_note(cls, value):
        return '' if value is None else value

    @field_validator('week')
    @classmethod
    def monday(cls, value):
        return WeeklyDemand.monday(value)


class BusinessInputs(InputRow):
    demands: list[WeeklyDemand] = Field(default_factory=list)
    confirmations: list[ConfirmationBatch] = Field(default_factory=list)


class InputSnapshot(InputRow):
    schema_version: Literal[1] = 1
    input_version: int = Field(default=0, ge=0, strict=True)
    masters: Masters = Field(default_factory=Masters)
    parameters: Parameters = Field(default_factory=Parameters)
    calendar: Calendar = Field(default_factory=Calendar)
    business: BusinessInputs = Field(default_factory=BusinessInputs)


def normalize_snapshot(payload: dict) -> InputSnapshot:
    return InputSnapshot.model_validate(payload)
