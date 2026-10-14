"""Explicitly injected external-boundary adapter. This does not validate Oracle."""
from auto_plan.access import Principal, AuthorizationDenied, LastAdministrator, IDLE_LIFETIME, ABSOLUTE_LIFETIME


class CompanyAuthentication:
    def verify(self, username, password):
        return password == "company-secret"
    def close(self):
        pass


class MemoryAccessStore:
    def __init__(self):
        self.users = {}
        self.sessions = {}
        self.events = []

    def startup(self):
        self.sessions.clear()
    def close(self):
        pass
    def login(self, user_id, token_hash, now, initial_admin):
        if not self.users and user_id == initial_admin:
            self.users[user_id] = {"user_id": user_id, "roles": ["administrator"], "active": True}
            self.events.append({"action": "bootstrap", "actor_id": user_id, "user_id": user_id, "roles": ["administrator"], "active": True})
        user = self.users.get(user_id)
        if not user or not user["active"] or not user["roles"]:
            raise AuthorizationDenied
        self.sessions[token_hash] = (user_id, now, now)
        return Principal(user_id, frozenset(user["roles"]))
    def session(self, token_hash, now):
        session = self.sessions.get(token_hash)
        if not session:
            return None
        user_id, created, seen = session
        user = self.users.get(user_id)
        if now - created >= ABSOLUTE_LIFETIME or now - seen >= IDLE_LIFETIME or not user or not user["active"] or not user["roles"]:
            self.sessions.pop(token_hash, None)
            return None
        self.sessions[token_hash] = (user_id, created, now)
        return Principal(user_id, frozenset(user["roles"]))
    def logout(self, token_hash):
        self.sessions.pop(token_hash, None)
    def _admin(self, actor):
        user = self.users.get(actor)
        if not user or not user["active"] or "administrator" not in user["roles"]:
            raise AuthorizationDenied
    def authorizations(self, actor_id):
        self._admin(actor_id)
        return list(self.users.values())
    def authorize(self, actor_id, user_id, roles, active, now):
        self._admin(actor_id)
        proposed = {"user_id": user_id, "roles": sorted(roles), "active": active}
        all_users = self.users | {user_id: proposed}
        if not any(u["active"] and "administrator" in u["roles"] for u in all_users.values()):
            raise LastAdministrator
        self.users[user_id] = proposed
        self.events.append({"action": "authorize", "actor_id": actor_id, **proposed})
        return proposed
    def audit(self, actor_id):
        self._admin(actor_id)
        return list(reversed(self.events))
