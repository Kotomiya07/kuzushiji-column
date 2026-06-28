from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import main


def test_app_startup_defaults_are_valid_for_local_dev() -> None:
    assert main.APP_HOST == "127.0.0.1"
    assert main.APP_PORT == 8100
    assert main.APP_RELOAD is True
    assert main.APP_RELOAD_DIRS == [str(Path(main.__file__).resolve().parent)]
    assert "output/*" in main.APP_RELOAD_EXCLUDES
