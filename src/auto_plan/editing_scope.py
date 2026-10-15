"""Shared date, line and model authorization independent of command registration."""
from datetime import date
from pydantic import Field, model_validator
from .input_models import InputRow, Code


class EditingScope(InputRow):
    start_date: date
    end_date: date
    line_codes: list[Code] = Field(min_length=1)
    model_codes: list[Code] = Field(default_factory=list)

    @model_validator(mode='after')
    def ordered(self):
        if self.start_date > self.end_date or len(set(self.line_codes)) != len(self.line_codes):
            raise ValueError('调整范围日期或线体重复，请重新选择')
        return self

    def contains(self, row):
        return self.start_date <= row.date <= self.end_date and row.line_code in self.line_codes and (not self.model_codes or row.model_code in self.model_codes)
