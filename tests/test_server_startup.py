from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import main


def test_app_startup_defaults_are_valid_for_local_dev() -> None:
    assert main.APP_HOST == "127.0.0.1"
    assert main.APP_PORT == 8100
    assert main.APP_RELOAD is False
    assert main.APP_RELOAD_DIRS == [str(Path(main.__file__).resolve().parent)]
    assert "output/*" in main.APP_RELOAD_EXCLUDES


def test_app_reload_is_opt_in_via_env() -> None:
    assert main.resolve_app_reload({}) is False
    assert main.resolve_app_reload({main.APP_RELOAD_ENV_VAR: ""}) is False
    assert main.resolve_app_reload({main.APP_RELOAD_ENV_VAR: "0"}) is False
    assert main.resolve_app_reload({main.APP_RELOAD_ENV_VAR: "false"}) is False
    assert main.resolve_app_reload({main.APP_RELOAD_ENV_VAR: "1"}) is True
    assert main.resolve_app_reload({main.APP_RELOAD_ENV_VAR: "true"}) is True
    assert main.resolve_app_reload({main.APP_RELOAD_ENV_VAR: "YES"}) is True
