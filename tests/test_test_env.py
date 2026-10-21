"""PLAN_TEST setup boundaries: secrets, account isolation and operator actions."""
import json
import os
import shlex
import stat
import subprocess

import pytest

from auto_plan import test_env


@pytest.fixture
def values():
    return {
        'AUTO_PLAN_ORACLE_USER': 'PLAN_TEST',
        'AUTO_PLAN_ORACLE_PASSWORD': 'fixture-password-$(do-not-execute)',
        'AUTO_PLAN_ORACLE_DSN': 'fixture-db:1521/test-service',
        'AUTO_PLAN_INITIAL_ADMIN_ID': 'TEST.ADMIN',
        'AUTO_PLAN_TEST_AUTH_ENABLED': 'true',
        'AUTO_PLAN_TEST_AUTH_PASSWORD': 'fixture-login-password',
        'AUTO_PLAN_PUBLIC_ORIGIN': 'https://test.example',
        'AUTO_PLAN_PROXY_TRUSTED_IPS': '10.0.0.1',
    }


def write_config(path, values):
    path.write_text(''.join(key + '=' + shlex.quote(value) + '\n' for key, value in values.items()))
    path.chmod(0o600)
    return path


def test_env_file_is_private_repeatable_and_preserves_literal_secrets(tmp_path, values):
    path = tmp_path / 'config' / 'test.env'
    test_env.init_env(path)
    test_env.init_env(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    values['AUTO_PLAN_ORACLE_PASSWORD'] = "literal ' quote # and $(touch NEVER) `command`"
    write_config(path, values)
    original = path.read_bytes()
    test_env.init_env(path)
    assert path.read_bytes() == original
    assert test_env.read_env(path) == values


def test_wizard_writer_round_trips_quotes_without_printing_secret(tmp_path):
    path = tmp_path / 'test.env'
    test_env.init_env(path)
    script = (test_env.REPO_ROOT / 'scripts/setup-test-env.sh').read_text()
    library = script[:script.index('# STAGES:')]
    password = "fixture ' quote $() # spaces \\ and `backticks`"
    result = subprocess.run(
        ['bash', '-c', library + '\nENV_FILE=$1\nwrite_env AUTO_PLAN_ORACLE_PASSWORD "$2"\n', 'writer-test', str(path), password],
        text=True, capture_output=True, check=True,
    )
    assert test_env.read_env(path)['AUTO_PLAN_ORACLE_PASSWORD'] == password
    assert password not in result.stdout + result.stderr


def test_initializer_runs_before_project_dependencies_are_installed(tmp_path):
    path = tmp_path / 'new-config' / 'test.env'
    result = subprocess.run(
        ['python3', str(test_env.REPO_ROOT / 'src/auto_plan/test_env.py'), 'init', '--env-file', str(path)],
        text=True, capture_output=True, check=True,
    )
    assert result.stderr == ''
    assert test_env.read_env(path) == {'AUTO_PLAN_ORACLE_USER': 'PLAN_TEST'}


def fake_uv(tmp_path, monkeypatch):
    binary_dir = tmp_path / 'bin'
    binary_dir.mkdir()
    binary = binary_dir / 'uv'
    binary.write_text("#!/usr/bin/env python3\nimport json,os,sys\nwith open(os.environ['SETUP_TEST_LOG'],'a') as f: f.write(json.dumps({'pid':os.getpid(),'argv':sys.argv[1:]})+'\\n')\n")
    binary.chmod(0o755)
    log = tmp_path / 'calls.jsonl'
    monkeypatch.setenv('PATH', str(binary_dir) + os.pathsep + os.environ['PATH'])
    monkeypatch.setenv('SETUP_TEST_LOG', str(log))
    return log


def test_start_preserves_process_identity_and_quotes_saved_path(tmp_path, values, monkeypatch):
    log = fake_uv(tmp_path, monkeypatch)
    path = write_config(tmp_path / 'config with spaces.env', values)
    script = test_env.REPO_ROOT / 'scripts/setup-test-env.sh'
    process = subprocess.Popen([str(script), '--start', '--env-file', str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    output, error = process.communicate(timeout=10)
    assert process.returncode == 0, error
    record = json.loads(log.read_text())
    assert record['pid'] == process.pid
    assert record['argv'][-4:] == ['auto_plan.test_env', 'serve', '--env-file', str(path)]
    assert values['AUTO_PLAN_ORACLE_PASSWORD'] not in output + error


@pytest.mark.parametrize('answer', ['', 'n\n'])
def test_maintenance_grant_never_runs_without_operator_confirmation(tmp_path, values, monkeypatch, answer):
    log = fake_uv(tmp_path, monkeypatch)
    path = write_config(tmp_path / 'test.env', values)
    result = subprocess.run(
        [str(test_env.REPO_ROOT / 'scripts/setup-test-env.sh'), '--grant-admin', '--env-file', str(path)],
        input=answer, text=True, capture_output=True, timeout=10,
    )
    assert result.returncode == 3
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(records) == 1
    assert records[0]['argv'][-3:] == ['validate', '--env-file', str(path)]
    assert values['AUTO_PLAN_ORACLE_PASSWORD'] not in result.stdout + result.stderr


@pytest.mark.parametrize('trusted', ['*', '10.0.0.1,*'])
def test_proxy_requires_explicit_trust(values, trusted):
    values['AUTO_PLAN_PROXY_TRUSTED_IPS'] = trusted
    with pytest.raises(test_env.SetupError):
        test_env.load_settings(values)


def test_config_rejects_checkout_paths_and_symlinks(tmp_path, values):
    with pytest.raises(test_env.SetupError):
        test_env.init_env(test_env.REPO_ROOT / '.test-secret.env')
    target = write_config(tmp_path / 'actual.env', values)
    link = tmp_path / 'link.env'
    link.symlink_to(target)
    with pytest.raises(test_env.SetupError):
        test_env.read_env(link)


def test_config_rejects_public_permissions_duplicate_and_unknown_keys(tmp_path, values):
    path = write_config(tmp_path / 'test.env', values)
    path.chmod(0o644)
    with pytest.raises(test_env.SetupError):
        test_env.read_env(path)
    path.chmod(0o600)
    with path.open('a') as file:
        file.write('AUTO_PLAN_ORACLE_PASSWORD=duplicate\n')
    with pytest.raises(test_env.SetupError):
        test_env.read_env(path)
    write_config(path, values)
    with path.open('a') as file:
        file.write('BASH_ENV=untrusted\n')
    with pytest.raises(test_env.SetupError):
        test_env.read_env(path)


def test_production_account_refused_before_connection(tmp_path, values, monkeypatch, capsys):
    values['AUTO_PLAN_ORACLE_USER'] = 'PLAN_PROD'
    path = write_config(tmp_path / 'test.env', values)
    def unexpected_connection(settings):
        pytest.fail('Production configuration reached Oracle connection')
    monkeypatch.setattr(test_env, 'connect', unexpected_connection)
    assert test_env.main(['grant-admin', '--env-file', str(path), '--confirm-user', 'TEST.ADMIN']) == 2
    output = capsys.readouterr().err
    assert 'PLAN_TEST' in output
    assert values['AUTO_PLAN_ORACLE_PASSWORD'] not in output


def test_saved_settings_are_independent_of_terminal_exports(values, monkeypatch):
    monkeypatch.setenv('AUTO_PLAN_ORACLE_USER', 'PLAN_PROD')
    monkeypatch.setenv('AUTO_PLAN_AUTH_CA_FILE', '/inherited/company-ca.crt')
    monkeypatch.setenv('AUTO_PLAN_JOB_WORKERS', '-9')
    settings = test_env.load_settings(values)
    assert settings.oracle_user == 'PLAN_TEST'
    assert settings.auth_ca_file == ''
    assert settings.job_workers is None
    assert settings.public_origin == values['AUTO_PLAN_PUBLIC_ORIGIN']


def test_invalid_settings_do_not_echo_secrets(tmp_path, values, capsys):
    values['AUTO_PLAN_PUBLIC_ORIGIN'] = 'http://invalid.example'
    path = write_config(tmp_path / 'test.env', values)
    assert test_env.main(['validate', '--env-file', str(path)]) == 2
    output = capsys.readouterr()
    assert values['AUTO_PLAN_ORACLE_PASSWORD'] not in output.out + output.err
    assert values['AUTO_PLAN_ORACLE_DSN'] not in output.out + output.err
    assert values['AUTO_PLAN_TEST_AUTH_PASSWORD'] not in output.out + output.err
    assert 'ValidationError' not in output.err


def test_grant_requires_the_configured_account_before_connect(tmp_path, values, monkeypatch):
    path = write_config(tmp_path / 'test.env', values)
    def unexpected_connection(settings):
        pytest.fail('Unconfirmed account reached Oracle connection')
    monkeypatch.setattr(test_env, 'connect', unexpected_connection)
    assert test_env.main(['grant-admin', '--env-file', str(path)]) == 2
    assert test_env.main(['grant-admin', '--env-file', str(path), '--confirm-user', 'OTHER']) == 2


class ReadConnection:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.queries = []
        self.rolled_back = False

    def __enter__(self): return self
    def __exit__(self, *args): pass
    def cursor(self): return self
    def execute(self, query, **params):
        assert query.startswith('SELECT ')
        self.queries.append(query)
    def fetchone(self): return next(self.responses)
    def rollback(self): self.rolled_back = True


class ReadStore:
    def __init__(self, responses):
        self.connection = ReadConnection(responses)
        self.pool = self
        self.closed = False

    def acquire(self): return self.connection
    def close(self): self.closed = True
    def startup(self): pytest.fail('Setup check must not invalidate sessions')


@pytest.mark.parametrize(('responses', 'expected'), [
    ([(0,)], 'bootstrap'), ([(17,), None], 'required'),
    ([(17,), (0, 1)], 'required'), ([(17,), (1, 0)], 'required'),
    ([(17,), (1, 1)], 'ready'),
])
def test_existing_authorizations_never_trigger_implicit_bootstrap(responses, expected):
    store = ReadStore(responses)
    assert test_env.admin_state(store, 'TEST.ADMIN') == expected
    assert store.connection.rolled_back


def test_connected_identity_checked_even_when_config_says_test(values, monkeypatch):
    from auto_plan.oracle_access import OracleAccessStore
    store = ReadStore([('PLAN_PROD',)])
    monkeypatch.setattr(OracleAccessStore, 'connect', lambda settings: store)
    with pytest.raises(test_env.SetupError):
        test_env.connect(test_env.load_settings(values))
    assert store.closed


def test_full_preflight_runs_all_checks_without_startup(tmp_path, values, monkeypatch):
    from auto_plan import schema, oracle_inputs, oracle_plans, oracle_demands, oracle_jobs
    settings = test_env.load_settings(values)
    settings.frontend_dist = tmp_path
    (tmp_path / 'index.html').write_text('built frontend')
    store = ReadStore([(17,), None])
    calls = []
    class Adapter:
        def __init__(self, boundary): pass
        def check_consistency(self): calls.append('check')
    monkeypatch.setattr(test_env, 'connect', lambda settings: store)
    monkeypatch.setattr(schema, 'verify_schema', lambda pool: calls.append('schema'))
    monkeypatch.setattr(oracle_inputs, 'OracleInputStore', Adapter)
    monkeypatch.setattr(oracle_plans, 'OraclePlanStore', Adapter)
    monkeypatch.setattr(oracle_demands, 'OracleDemandStore', Adapter)
    monkeypatch.setattr(oracle_jobs, 'OracleJobStore', Adapter)
    assert test_env.check_environment(settings) == test_env.ADMIN_REQUIRED
    assert calls == ['schema', 'check', 'check', 'check', 'check']
    assert store.closed


def test_serve_uses_existing_entrypoint_and_only_saved_app_settings(tmp_path, values, monkeypatch):
    path = write_config(tmp_path / 'test.env', values)
    monkeypatch.setenv('AUTO_PLAN_ORACLE_USER', 'PLAN_PROD')
    monkeypatch.setenv('AUTO_PLAN_AUTH_CA_FILE', 'not-in-file')
    monkeypatch.setattr(test_env, 'check_environment', lambda settings: 0)
    monkeypatch.chdir(tmp_path)
    class ExecStopped(BaseException): pass
    captured = {}
    def execve(executable, argv, environment):
        captured.update(executable=executable, argv=argv, environment=environment)
        raise ExecStopped
    monkeypatch.setattr(os, 'execve', execve)
    with pytest.raises(ExecStopped):
        test_env.main(['serve', '--env-file', str(path)])
    assert captured['argv'][1:] == ['-m', 'auto_plan.serve']
    app_env = {k: v for k, v in captured['environment'].items() if k.startswith('AUTO_PLAN_')}
    assert app_env == values
