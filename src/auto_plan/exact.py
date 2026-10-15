"""Lossless rational time values; display strings never participate in arithmetic."""
import re
from fractions import Fraction
from functools import lru_cache
from typing import Annotated
from pydantic import BeforeValidator
from .input_models import InputRow


def exact_value(value):
    if not isinstance(value, str) or len(value) > 1000 or not re.fullmatch(r'\d+(?:\.\d+|/\d+)?', value.strip()):
        raise ValueError('工时必须为非负十进制或分数文本，不能使用浮点数')
    try:
        number = Fraction(value.strip())
    except (ValueError, ZeroDivisionError):
        raise ValueError('精确值的分母必须大于零') from None
    return str(number)


Exact = Annotated[str, BeforeValidator(exact_value)]


class Hours(InputRow):
    exact: Exact
    display: str


def hours(value: Fraction | str | int) -> Hours:
    return _hours(Fraction(value))


@lru_cache(maxsize=1024)
def _hours(number: Fraction) -> Hours:
    # Hours is frozen and contains only immutable strings; result collections
    # and evaluated plans are never cached here.
    if 0 < number < Fraction(1, 10000):
        display = '<0.0001'
    else:
        rounded = (number.numerator * 20000 + number.denominator) // (2 * number.denominator)
        display = f'{rounded // 10000}.{rounded % 10000:04d}'
    return Hours(exact=str(number), display=display)
