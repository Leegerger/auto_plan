"""Owned planning fields from an already verified immutable JSON document."""
import json
from typing import Any, NotRequired, TypedDict
from pydantic import ConfigDict, TypeAdapter, with_config


@with_config(ConfigDict(extra='ignore', cache_strings=False))
class PlanningSnapshot(TypedDict):
    plan: Any
    time: NotRequired[Any]


@with_config(ConfigDict(extra='ignore', cache_strings=False))
class PlanningRecord(TypedDict):
    snapshot: PlanningSnapshot
    coverage: NotRequired[Any]
    lineage: NotRequired[Any]
    unallocated: NotRequired[Any]
    operation_issues: NotRequired[Any]
    kind: NotRequired[Any]
    pending: NotRequired[Any]
    operations: NotRequired[Any]
    parent_revision: NotRequired[Any]
    revision: NotRequired[Any]
    latest_adoption: NotRequired[Any]
    actor: NotRequired[Any]
    recorded_at: NotRequired[Any]
    input_version: NotRequired[Any]


_planning_record = TypeAdapter(PlanningRecord)


def planning_document(raw):
    """Skip historical evaluation/inputs; retain legacy exceptional numbers."""
    try:
        record = _planning_record.validate_json(raw)
    except ValueError:
        return json.loads(raw)
    pending = [record]
    while pending:
        value = pending.pop()
        # Preserve stdlib float precision/nonfinite acceptance and its own
        # arbitrary-integer limit instead of inheriting parser-specific limits.
        if type(value) is float or type(value) is int and value.bit_length() > 4096:
            return json.loads(raw)
        if type(value) is dict:
            pending.extend(value.values())
        elif type(value) is list:
            pending.extend(value)
    return record
