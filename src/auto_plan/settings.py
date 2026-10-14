from pathlib import Path
from urllib.parse import urlsplit
from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTO_PLAN_", extra="ignore")
    base_path: str = "/auto_plan"
    public_origin: str = "https://fsscm.cminl.oa"
    initial_admin_id: str = ""
    oracle_user: str = "PLAN_TEST"
    oracle_password: SecretStr = SecretStr("")
    oracle_dsn: str = ""
    oracle_client_path: str = "/opt/oracle/instantclient_21_13"
    oracle_pool_max: int = 10
    auth_url: str = "https://psamv4athefs.cminl.oa/api/SSO/Login"
    auth_ca_file: str = ""
    auth_user_type: str = "Auto"
    auth_sys_id: str = "FS_MOD-SCM_PORTAL"
    test_auth_enabled: bool = False
    test_auth_password: SecretStr = SecretStr("")
    frontend_dist: Path = Path("frontend/dist")

    @field_validator("base_path")
    @classmethod
    def valid_prefix(cls, value):
        value = value.rstrip("/")
        if value and (not value.startswith("/") or any(c not in "/_-abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" for c in value) or "//" in value):
            raise ValueError("Invalid application path prefix")
        return value

    @field_validator("public_origin")
    @classmethod
    def valid_origin(cls, value):
        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.netloc or parts.path or parts.query or parts.fragment or parts.username:
            raise ValueError("public_origin must be an HTTPS origin without a path")
        return value

    @field_validator("oracle_pool_max")
    @classmethod
    def valid_pool(cls, value):
        if value < 1:
            raise ValueError("oracle_pool_max must be positive")
        return value

    @model_validator(mode="after")
    def test_auth_guard(self):
        if self.test_auth_enabled and self.oracle_user.strip().upper() != "PLAN_TEST":
            raise ValueError("Test authentication requires PLAN_TEST; PLAN_PROD forbids it")
        if self.test_auth_enabled and not self.test_auth_password.get_secret_value():
            raise ValueError("Test authentication requires an explicit test password")
        if urlsplit(self.auth_url).scheme != "https":
            raise ValueError("Company authentication requires HTTPS")
        return self
