from pathlib import Path
import subprocess
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import main


def test_build_hf_update_command_uses_converter_project_and_saved_annotations() -> None:
    config = main.HfDatasetUpdateRequest(
        dataset_type="page",
        bbox_format="yolo",
        hub_username="test-org",
        dataset_name_prefix="kuzushiji-test",
    )

    command = main.build_hf_update_command(config)

    project_root = Path(main.__file__).resolve().parent
    converter_dir = (project_root / main.HF_CONVERTER_DIR).resolve()

    assert command[:4] == ["uv", "run", "--project", str(converter_dir)]
    assert str(converter_dir / "convert_dataset.py") in command
    assert "--push-to-hub" in command
    assert "--hub-token" not in command
    assert command[command.index("--raw-dir") + 1] == str((project_root / "raw").resolve())
    assert command[command.index("--column-annotations-dir") + 1] == str(
        (project_root / "output").resolve()
    )
    assert command[command.index("--segment-annotations-dir") + 1] == str(
        (project_root / "output_seg").resolve()
    )
    assert command[command.index("--output-dir") + 1] == str(
        (project_root / main.HF_UPDATE_OUTPUT_DIR).resolve()
    )
    assert command[command.index("--dataset-type") + 1] == "page"
    assert command[command.index("--bbox-format") + 1] == "yolo"
    assert command[command.index("--hub-username") + 1] == "test-org"


def test_run_hf_update_job_records_success_and_repo_urls() -> None:
    main.reset_hf_update_state_for_tests()
    config = main.HfDatasetUpdateRequest(dataset_type="both", bbox_format="coco")
    job = main.create_hf_update_job(config)

    def fake_runner(
        command: list[str],
        cwd: Path,
        text: bool,
        capture_output: bool,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                "Dataset pushed to: https://huggingface.co/datasets/user/kuzushiji-dataset-coco\n"
                "Dataset pushed to: https://huggingface.co/datasets/user/kuzushiji-dataset-characters\n"
            ),
            stderr="",
        )

    main.run_hf_update_job(str(job["job_id"]), config, runner=fake_runner)

    status = main.get_hf_update_status_payload()
    assert status["status"] == "completed"
    assert status["returncode"] == 0
    assert status["repo_urls"] == [
        "https://huggingface.co/datasets/user/kuzushiji-dataset-coco",
        "https://huggingface.co/datasets/user/kuzushiji-dataset-characters",
    ]
    assert status["finished_at"] is not None


def test_create_hf_update_job_rejects_parallel_running_job() -> None:
    main.reset_hf_update_state_for_tests()
    config = main.HfDatasetUpdateRequest()

    first = main.create_hf_update_job(config)
    second = main.create_hf_update_job(config)

    assert first["started"] is True
    assert second["started"] is False
    assert second["status"] == "running"
