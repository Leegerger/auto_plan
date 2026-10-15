"""Durable calculation commands. Sessions and browser drafts never enter jobs."""
from uuid import UUID
from pydantic import Field
from .input_models import InputRow, Pieces
from .solver_models import SolveRequest, FrozenSolveInput
from .plans import PlanProblem


class JobUnknown(Exception):
    """A durable transaction may have committed; verification is required."""


class SubmitJob(InputRow):
    submission_id: UUID
    base_revision: Pieces
    input_version: Pieces
    latest_adoption: Pieces
    request: SolveRequest


class JobKey(InputRow):
    submission_id: UUID


class JobService:
    def __init__(self, store, planner):
        self.store, self.planner = store, planner

    def submit(self, command, actor, now):
        def freeze(unit):
            if (command.base_revision, command.input_version, command.latest_adoption) != (unit.revision, unit.inputs.input_version, unit.adoption):
                raise PlanProblem(409, '共享草稿、完整输入或采用版已变化，请刷新后提交')
            return FrozenSolveInput(snapshot=self.planner._snapshot(unit, unit.plan, now),
                source_revision=unit.revision, input_version=unit.inputs.input_version,
                latest_adoption=unit.adoption, request=command.request.model_copy(update={'native_threads':1}))
        return self.store.submit(command, actor, now, freeze)

    def rerun(self, identity, command, actor, now):
        return self.store.rerun(identity, command, actor, now)

    def verify(self, key, actor, now):
        return self.store.verify(str(key), actor, now)

    def list(self, actor, now):
        return self.store.list(actor, now)

    def get(self, identity, actor, now):
        return self.store.get(identity, actor, now)

    def candidate(self, identity, candidate, actor, now):
        return self.store.candidate(identity, candidate, actor, now)

    def stop(self, identity, actor, now):
        return self.store.stop(identity, actor, now)
