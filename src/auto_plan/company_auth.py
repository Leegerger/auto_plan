import hmac
import ssl
import httpx
from .settings import Settings


class AuthenticationUnavailable(Exception):
    pass


class CompanyAuthenticator:
    """The company endpoint alone receives legacy TLS compatibility."""
    def __init__(self, settings: Settings, *, transport=None):
        if not settings.auth_ca_file:
            raise ValueError("AUTO_PLAN_AUTH_CA_FILE must point to the mounted internal root CA")
        context = ssl.create_default_context(cafile=settings.auth_ca_file)
        context.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
        self.client = httpx.Client(verify=context, trust_env=False, timeout=httpx.Timeout(15), transport=transport)
        self.settings = settings

    def verify(self, username: str, password: str) -> bool:
        try:
            response = self.client.post(self.settings.auth_url, json={
                "UserID": username.strip(), "Password": password,
                "UserType": self.settings.auth_user_type, "SysID": self.settings.auth_sys_id,
            })
        except httpx.HTTPError:
            raise AuthenticationUnavailable from None
        if response.status_code == 200:
            return True
        if response.status_code == 401:
            return False
        raise AuthenticationUnavailable

    def close(self):
        self.client.close()


class TestAuthenticator:
    def __init__(self, settings: Settings):
        # Settings also enforce this guard before any connection or server startup.
        if not settings.test_auth_enabled or settings.oracle_user.upper() != "PLAN_TEST":
            raise ValueError("Test authentication is disabled")
        self.password = settings.test_auth_password.get_secret_value()

    def verify(self, username: str, password: str) -> bool:
        return hmac.compare_digest(password, self.password)

    def close(self):
        pass
