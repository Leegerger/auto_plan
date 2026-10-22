"""Explicit real-Oracle setup adapter for immutable legacy input fixtures."""
from datetime import datetime

from auto_plan.input_models import InputSnapshot
from auto_plan.system_actors import SYSTEM_MAINTENANCE_ACTOR


def seed_legacy_input_revision(store, snapshot: InputSnapshot, now: datetime) -> None:
    """Persist revision 1 as fixture data before assertions use the public API."""
    if snapshot.input_version != 1:
        raise ValueError('Legacy fixture snapshot must be revision 1')
    store.update(
        SYSTEM_MAINTENANCE_ACTOR,
        now,
        lambda current: snapshot.model_copy(update={'input_version': current.input_version + 1}),
    )
