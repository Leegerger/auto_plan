"""Decode immutable persisted snapshots by their stored schema version."""
from collections.abc import Callable, Mapping

from .input_models import InputSnapshot
from .plan_models import EvaluationSnapshot


def _decode_input_schema_1(payload: Mapping) -> InputSnapshot:
    return InputSnapshot.model_validate(payload)


INPUT_SCHEMA_DECODERS: dict[int, Callable[[Mapping], InputSnapshot]] = {
    1: _decode_input_schema_1,
}


def _versioned_input_payload(payload: Mapping) -> InputSnapshot:
    version = payload.get('schema_version', 1)
    decoder = INPUT_SCHEMA_DECODERS.get(version) if type(version) is int else None
    if decoder is None:
        raise ValueError('Unsupported stored input schema version')
    return decoder(payload)


def decode_stored_input(payload: Mapping) -> InputSnapshot:
    """Interpret one persisted input document through its stored schema."""
    return _versioned_input_payload(payload)


def evaluation_payload(payload: Mapping):
    """Return a stored evaluation payload with its pinned input decoded."""
    if 'inputs' not in payload:
        raise ValueError('Stored evaluation is missing inputs')
    values = dict(payload)
    values['inputs'] = decode_stored_input(values['inputs'])
    return values


def decode_stored_evaluation(payload: Mapping) -> EvaluationSnapshot:
    """Interpret a persisted evaluation while keeping its input revision pinned."""
    values = evaluation_payload(payload)
    return EvaluationSnapshot.model_validate(values)


def decode_stored_frozen_solve(payload: Mapping):
    from .solver_models import FrozenSolveInput
    return _decode_nested_snapshot(payload, FrozenSolveInput, 'frozen solve')


def decode_stored_candidate(payload: Mapping):
    from .solver_models import Candidate
    return _decode_nested_snapshot(payload, Candidate, 'candidate')


def _decode_nested_snapshot(payload: Mapping, model, label: str):
    if 'snapshot' not in payload:
        raise ValueError(f'Stored {label} is missing its snapshot')
    values = dict(payload)
    values['snapshot'] = decode_stored_evaluation(values['snapshot'])
    return model.model_validate(values)
