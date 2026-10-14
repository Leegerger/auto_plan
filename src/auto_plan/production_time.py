"""Beijing production dates and weeks have an 08:00 boundary."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

BEIJING = ZoneInfo('Asia/Shanghai')


def production_date(instant: datetime) -> date:
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError('计算时点必须包含时区')
    return (instant.astimezone(BEIJING) - timedelta(hours=8)).date()


def production_week(day: date) -> date:
    return day - timedelta(days=day.weekday())


def production_cutoff(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 8, tzinfo=BEIJING)
