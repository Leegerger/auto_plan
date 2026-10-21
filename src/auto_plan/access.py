from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

ROLES = frozenset({"viewer", "editor", "administrator"})
IDLE_LIFETIME = timedelta(hours=2)
ABSOLUTE_LIFETIME = timedelta(hours=8)


@dataclass(frozen=True)
class Principal:
    user_id: str
    roles: frozenset[str]

    def public(self):
        return {"user_id": self.user_id, "roles": sorted(self.roles)}


class StoreUnavailable(Exception):
    pass


class LastAdministrator(Exception):
    pass


class AuthorizationDenied(Exception):
    pass


class AccessStore(Protocol):
    def startup(self) -> None: ...
    def close(self) -> None: ...
    def login(self, user_id: str, token_hash: str, now: datetime, initial_admin: str) -> Principal: ...
    def session(self, token_hash: str, now: datetime) -> Principal | None: ...
    def logout(self, token_hash: str) -> None: ...
    def authorizations(self, actor_id: str) -> list[dict]: ...
    def authorize(self, actor_id: str, user_id: str, roles: frozenset[str], active: bool, now: datetime) -> dict: ...
    def audit(self, actor_id: str) -> list[dict]: ...


def normalize_account(value: str) -> str:
    return value.strip().upper()
