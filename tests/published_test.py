from pathlib import Path

import httpx2
import pytest
from huggingface_hub.errors import GatedRepoError

from ic_deck_bench import published


def test_a_gated_dataset_says_how_to_get_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def gated(*_: object, **__: object) -> str:
        raise GatedRepoError(
            "gated",
            response=httpx2.Response(
                401, request=httpx2.Request("GET", "https://huggingface.co")
            ),
        )

    monkeypatch.setattr(published, "HF_REVISION", "a" * 40)
    monkeypatch.setattr(published, "snapshot_download", gated)
    with pytest.raises(published.DatasetLocked) as locked:
        published.fetched(None)
    assert (
        f"GatedRepoError: {published.HF_REPO} is gated; accept its terms at "
        f"{published.DATASET_URL}, then run `huggingface-cli login` or set HF_TOKEN"
    ) == str(locked.value)


def test_a_run_naming_a_file_outside_its_folder_is_refused(tmp_path: Path) -> None:
    folder = tmp_path / published.RUNS / "voltaic" / "row"
    folder.mkdir(parents=True)
    (folder / published.RUN_MANIFEST).write_text(
        '{"answers": [], "files": ["../../../../secret"], "deleted": []}'
    )
    with pytest.raises(ValueError):
        published.row_result(tmp_path, "voltaic", "row")
