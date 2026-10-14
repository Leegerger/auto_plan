"""Explicit test-only Oracle boundary adapter, never a runtime fallback."""
from threading import RLock
from auto_plan.input_models import InputSnapshot


class MemoryInputStore:
    def __init__(self):
        self.lock = RLock()
        self.versions = {0: InputSnapshot()}
        self.version = 0

    def read(self, version=None):
        with self.lock:
            return self.versions[self.version if version is None else version].model_copy(deep=True)

    def update(self, actor, now, transform):
        with self.lock:
            candidate = transform(self.read())
            self.versions[candidate.input_version] = candidate.model_copy(deep=True)
            self.version = candidate.input_version
            return candidate
