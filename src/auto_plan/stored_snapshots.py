"""Decode immutable persisted snapshots by their stored schema version."""
from collections.abc import Mapping

from .input_models import InputSnapshot
from .plan_models import EvaluationSnapshot


def _versioned_input_payload(payload: Mapping):
    version = payload.get('schema_version', 1)
    if type(version) is not int or version != 1:
        raise ValueError('Unsupported stored input schema version')
    return payload


def decode_stored_input(payload: Mapping) -> InputSnapshot:
    """Interpret one persisted input document through its stored schema."""
    return InputSnapshot.model_validate(_versioned_input_payload(payload))


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
    if 'snapshot' not in payload:
        raise ValueError('Stored frozen solve is missing its snapshot')
    values = dict(payload)
    values['snapshot'] = decode_stored_evaluation(values['snapshot'])
    return FrozenSolveInput.model_validate(values)


def decode_stored_candidate(payload: Mapping):
    from .solver_models import Candidate
    if 'snapshot' not in payload:
        raise ValueError('Stored candidate is missing its snapshot')
    values = dict(payload)
    values['snapshot'] = decode_stored_evaluation(values['snapshot'])
    return Candidate.model_validate(values)
