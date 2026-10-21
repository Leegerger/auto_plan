"""Operator tools for a configured PLAN_TEST environment, separate from startup."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shlex
import stat
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_KEYS = frozenset({
    'AUTO_PLAN_ORACLE_USER', 'AUTO_PLAN_ORACLE_PASSWORD', 'AUTO_PLAN_ORACLE_DSN',
    'AUTO_PLAN_ORACLE_CLIENT_PATH', 'AUTO_PLAN_ORACLE_POOL_MAX',
    'AUTO_PLAN_INITIAL_ADMIN_ID', 'AUTO_PLAN_PUBLIC_ORIGIN', 'AUTO_PLAN_BASE_PATH',
    'AUTO_PLAN_PROXY_TRUSTED_IPS', 'AUTO_PLAN_TEST_AUTH_ENABLED',
    'AUTO_PLAN_TEST_AUTH_PASSWORD', 'AUTO_PLAN_AUTH_CA_FILE', 'AUTO_PLAN_AUTH_URL',
    'AUTO_PLAN_AUTH_USER_TYPE', 'AUTO_PLAN_AUTH_SYS_ID', 'AUTO_PLAN_JOB_WORKERS',
    'AUTO_PLAN_JOB_QUEUE_CAPACITY', 'AUTO_PLAN_ADOPTION_SPACE_WARNING_BYTES',
})
ADMIN_REQUIRED = 3


class SetupError(Exception):
    """Only fixed, secret-free instructions may be shown to the operator."""


def env_path(value: str | Path) -> Path:
    path = Path(value).expanduser().absolute()
    if path.resolve().is_relative_to(REPO_ROOT):
        raise SetupError('設定檔必須放在專案目錄之外。')
    if path.is_symlink():
        raise SetupError('設定檔不可使用符號連結。')
    return path


def read_env(value: str | Path) -> dict[str, str]:
    path = env_path(value)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise SetupError('設定檔必須是目前使用者擁有的一般檔案。')
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise SetupError('請先將設定檔權限設為 600。')
    try:
        words = shlex.split(path.read_text(), comments=True)
    except ValueError:
        raise SetupError('設定檔格式錯誤，請重新執行設定腳本。') from None
    result = {}
    for word in words:
        key, separator, item = word.partition('=')
        if not separator or key not in ENV_KEYS or key in result:
            raise SetupError('設定檔包含不支援或重複的變數。')
        result[key] = item
    if result.get('AUTO_PLAN_ORACLE_USER', '').strip().upper() != 'PLAN_TEST':
        raise SetupError('此工具只允許 AUTO_PLAN_ORACLE_USER=PLAN_TEST。')
    result['AUTO_PLAN_ORACLE_USER'] = 'PLAN_TEST'
    return result


def init_env(value: str | Path) -> None:
    path = env_path(value)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Validate an existing file before the wizard offers its saved values.
        read_env(path)
    else:
        with os.fdopen(descriptor, 'w') as file:
            file.write("AUTO_PLAN_ORACLE_USER='PLAN_TEST'\n")


def load_settings(values: dict[str, str]):
    from .access import normalize_account
    from .settings import Settings
    if values.get('AUTO_PLAN_ORACLE_USER', '').strip().upper() != 'PLAN_TEST':
        raise SetupError('此工具只允許 PLAN_TEST。')
    # A saved setup must behave the same from a clean terminal. Do not inherit
    # unrelated AUTO_PLAN_* settings from the caller during preflight.
    configured = {name: field.get_default(call_default_factory=True) for name, field in Settings.model_fields.items() if not field.is_required()}
    configured.update({key.removeprefix('AUTO_PLAN_').lower(): value for key, value in values.items()})
    settings = Settings(**configured)
    if settings.oracle_user.strip().upper() != 'PLAN_TEST':
        raise SetupError('此工具只允許 PLAN_TEST。')
    if not settings.oracle_dsn or not settings.oracle_password.get_secret_value():
        raise SetupError('請設定 Oracle DSN 與密碼。')
    account = normalize_account(settings.initial_admin_id)
    if not account or len(account) > 128 or any(ord(char) < 32 for char in account):
        raise SetupError('請設定有效的初始管理員帳號。')
    if '*' in settings.proxy_trusted_ips:
        raise SetupError('請設定實際反代 IP，不要使用萬用字元。')
    return settings


def connect(settings):
    from .oracle_access import OracleAccessStore
    store = OracleAccessStore.connect(settings)
    try:
        with store.pool.acquire() as connection:
            connection.call_timeout = 15000
            with connection.cursor() as cursor:
                cursor.execute("SELECT SYS_CONTEXT('USERENV','SESSION_USER') FROM DUAL")
                if cursor.fetchone()[0] != 'PLAN_TEST':
                    raise SetupError('實際連線帳號不是 PLAN_TEST，已停止。')
            connection.rollback()
    except Exception:
        store.close()
        raise
    return store


def admin_state(store, account: str) -> str:
    with store.pool.acquire() as connection:
        connection.call_timeout = 15000
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT COUNT(*) FROM AP_AUTHORIZATION')
                if cursor.fetchone()[0] == 0:
                    return 'bootstrap'
                cursor.execute('SELECT ADMINISTRATOR,ACTIVE FROM AP_AUTHORIZATION WHERE USER_ID=:user_id', user_id=account)
                row = cursor.fetchone()
                return 'ready' if row and row[0] and row[1] else 'required'
        finally:
            connection.rollback()


def check_environment(settings) -> int:
    from .access import normalize_account
    from .company_auth import CompanyAuthenticator, TestAuthenticator
    from .oracle_inputs import OracleInputStore
    from .oracle_plans import OraclePlanStore
    from .oracle_demands import OracleDemandStore
    from .oracle_jobs import OracleJobStore
    from .schema import verify_schema

    auth = TestAuthenticator(settings) if settings.test_auth_enabled else CompanyAuthenticator(settings)
    auth.close()
    if not (settings.frontend_dist / 'index.html').is_file():
        raise SetupError('前端尚未建置，請執行 npm run build --prefix frontend。')
    store = connect(settings)
    try:
        try:
            verify_schema(store.pool)
        except Exception:
            raise SetupError('schema 或台帳不相容，請依 docs/deployment.md 完成遷移；此工具不會自動 baseline 或重建資料表。') from None
        plans = OraclePlanStore(store.pool)
        for name, adapter in (
            ('輸入', OracleInputStore(store.pool)), ('計畫', plans),
            ('需求', OracleDemandStore(plans)), ('任務', OracleJobStore(store.pool)),
        ):
            try:
                adapter.check_consistency()
            except Exception:
                raise SetupError(name + '資料一致性檢查失敗，請先檢查既有資料。') from None
            print(name + '資料檢查通過。', flush=True)
        account = normalize_account(settings.initial_admin_id)
        state = admin_state(store, account)
        if state == 'required':
            print('已有授權資料，INITIAL_ADMIN_ID 不會自動授權；指定帳號仍需有效的管理員權限。', flush=True)
            return ADMIN_REQUIRED
        if state == 'bootstrap':
            print('授權表為空，指定帳號首次驗證成功後會建立管理員。', flush=True)
        else:
            print('指定帳號已有有效管理員權限。', flush=True)
        print('資料庫檢查完成；登入驗證與 HTTPS 仍需由瀏覽器實際確認。', flush=True)
        return 0
    finally:
        store.close()


def grant_admin(store, account: str, now: datetime) -> None:
    """Explicit PLAN_TEST maintenance grant; preserve other roles and audit it."""
    with store.transaction() as cursor:
        store._lock(cursor)
        cursor.execute('MERGE INTO AP_AUTHORIZATION A USING (SELECT :user_id USER_ID FROM DUAL) S ON (A.USER_ID=S.USER_ID) WHEN MATCHED THEN UPDATE SET A.ADMINISTRATOR=1,A.ACTIVE=1 WHEN NOT MATCHED THEN INSERT (USER_ID,ADMINISTRATOR,ACTIVE) VALUES (:user_id,1,1)', user_id=account)
        cursor.execute('SELECT VIEWER,EDITOR,ADMINISTRATOR FROM AP_AUTHORIZATION WHERE USER_ID=:user_id', user_id=account)
        row = cursor.fetchone()
        roles = {name for name, enabled in zip(('viewer', 'editor', 'administrator'), row) if enabled}
        store._audit(cursor, 'PLAN_TEST_SETUP', account, 'authorize', roles, True, now)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('init', 'validate', 'check', 'grant-admin', 'serve'))
    parser.add_argument('--env-file', required=True)
    parser.add_argument('--confirm-user')
    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            init_env(args.env_file)
            return 0
        values = read_env(args.env_file)
        settings = load_settings(values)
        if args.command == 'validate':
            print('設定格式檢查通過。')
            return 0
        if args.command == 'check':
            return check_environment(settings)
        if args.command == 'grant-admin':
            from .access import normalize_account
            from .schema import verify_schema
            account = normalize_account(settings.initial_admin_id)
            if not args.confirm_user or normalize_account(args.confirm_user) != account:
                raise SetupError('維護授權需要明確確認設定中的帳號。')
            store = connect(settings)
            try:
                verify_schema(store.pool)
                grant_admin(store, account, datetime.now(timezone.utc))
            finally:
                store.close()
            print('管理員權限已啟用並寫入授權審計；既有其他角色保留。')
            return 0
        result = check_environment(settings)
        if result:
            return result
        # Run the existing signal-aware server. Never source/eval the env file.
        environment = {key: value for key, value in os.environ.items() if not key.startswith('AUTO_PLAN_')}
        environment.update(values)
        os.chdir(REPO_ROOT)
        print('啟動後請開啟 ' + settings.public_origin + settings.base_path + '/', flush=True)
        os.execve(sys.executable, [sys.executable, '-m', 'auto_plan.serve'], environment)
    except SetupError as error:
        print('ERROR auto_plan.test_env ' + str(error), file=sys.stderr)
    except Exception:
        # Pydantic, Oracle and SSL exception strings can include secrets.
        print('ERROR auto_plan.test_env 設定或連線失敗，請檢查設定檔、Oracle Client、CA 與網路。', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
