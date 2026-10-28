from unittest.mock import AsyncMock, Mock

import pytest

from github_radar.recovery import (
    RESTORE_TARGET,
    _dump,
    _restore,
    pg_environment,
    recovery_drill,
    require_empty,
    validate_target,
)

SOURCE = "postgresql+asyncpg://local:password@127.0.0.1/github_radar"
TARGET = "postgresql+asyncpg://local:password@127.0.0.1/" + RESTORE_TARGET


@pytest.mark.parametrize(
    "database", ["github_radar", "github_radar_test", "other_test", "", "postgres"]
)
async def test_restore_refuses_every_non_allowlisted_database(database, monkeypatch, tmp_path):
    engine = Mock(side_effect=AssertionError("unsafe connection"))
    monkeypatch.setattr("github_radar.recovery.make_engine", engine)
    with pytest.raises(ValueError):
        await recovery_drill(
            SOURCE,
            TARGET.replace(RESTORE_TARGET, database),
            RESTORE_TARGET,
            tmp_path / "backup.dump",
        )
    engine.assert_not_called()


@pytest.mark.parametrize(
    "target",
    [
        TARGET + "?host=evil",
        TARGET.replace("127.0.0.1", "evil.test"),
        "bad",
        "",
        TARGET.replace("asyncpg", "psycopg"),
    ],
)
def test_restore_rejects_ambiguous_connection(target):
    with pytest.raises(ValueError):
        validate_target(SOURCE, target, RESTORE_TARGET)


def test_restore_requires_confirmation_and_distinct_source():
    assert validate_target(SOURCE, TARGET, RESTORE_TARGET).database == RESTORE_TARGET
    with pytest.raises(ValueError):
        validate_target(SOURCE, TARGET, "yes")
    with pytest.raises(ValueError):
        validate_target(TARGET, TARGET, RESTORE_TARGET)


async def test_restore_refuses_even_empty_existing_objects():
    connection = AsyncMock(scalar=AsyncMock(return_value=True))
    with pytest.raises(ValueError, match="not empty"):
        await require_empty(connection)


def test_commands_never_replay_owners_create_or_clean(monkeypatch, tmp_path):
    monkeypatch.setenv("PGSERVICE", "production")
    monkeypatch.setenv("PGOPTIONS", "unsafe")
    env = pg_environment(validate_target(SOURCE, TARGET, RESTORE_TARGET))
    assert "PGSERVICE" not in env and "unsafe" not in env.values()
    run = Mock()
    monkeypatch.setattr("github_radar.recovery.subprocess.run", run)
    output = tmp_path / "archive.dump"
    _dump(output, env, "snapshot-id")
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        _dump(output, env, "snapshot-id")
    _restore(output, env)
    command = run.call_args.args[0]
    assert command[command.index("--dbname") + 1] == RESTORE_TARGET
    assert "--single-transaction" in command and "--exit-on-error" in command
    assert not {"--clean", "--create"}.intersection(command)
    assert "password" not in " ".join(command)
