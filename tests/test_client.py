from pathlib import Path

import pytest
import responses

from pku_disk.client import DEFAULT_ROOT_ID, AnyShareClient, AnyShareError


@responses.activate
def test_list_root_and_resolve_unicode_path() -> None:
    url = "https://disk.pku.edu.cn/api/efast/v1/folders/gns%3A%2F%2F43FC0471AD1C458B91E339426F7909C4/sub_objects"
    responses.get(
        url,
        json={
            "dirs": [{"id": DEFAULT_ROOT_ID + "/A", "name": "实验数据", "size": -1}],
            "files": [{"id": DEFAULT_ROOT_ID + "/B", "name": "model.pt", "size": 42}],
            "next_marker": "",
        },
    )
    client = AnyShareClient("test-token")
    entries = client.list_path("/")
    assert [item.name for item in entries] == ["实验数据", "model.pt"]
    assert client.resolve("/实验数据").kind == "dir"


@responses.activate
def test_expired_token_has_actionable_error() -> None:
    responses.get(
        "https://disk.pku.edu.cn/api/efast/v1/folders/gns%3A%2F%2F43FC0471AD1C458B91E339426F7909C4/sub_objects",
        status=401,
    )
    with pytest.raises(AnyShareError, match="auth login"):
        AnyShareClient("expired").list_path("/")


def test_download_refuses_existing_destination(tmp_path: Path) -> None:
    destination = tmp_path / "existing.bin"
    destination.write_bytes(b"keep")
    client = AnyShareClient("test-token")
    client.resolve = lambda *_args, **_kwargs: type(
        "Item", (), {"id": "gns://file", "name": "existing.bin", "kind": "file"}
    )()
    with pytest.raises(AnyShareError, match="already exists"):
        client.download("/existing.bin", destination)
    assert destination.read_bytes() == b"keep"


@responses.activate
def test_upload_uses_signed_object_storage_request(tmp_path: Path) -> None:
    local = tmp_path / "result.bin"
    local.write_bytes(b"result")
    root_url = "https://disk.pku.edu.cn/api/efast/v1/folders/gns%3A%2F%2F43FC0471AD1C458B91E339426F7909C4/sub_objects"
    responses.get(root_url, json={"dirs": [], "files": [], "next_marker": ""})
    responses.post(
        "https://disk.pku.edu.cn/api/efast/v1/file/osbeginupload",
        json={
            "authrequest": [
                "POST",
                "https://objects.example/upload",
                "key: signed-key",
            ],
            "docid": DEFAULT_ROOT_ID + "/NEW",
            "rev": "REV1",
        },
    )
    responses.post("https://objects.example/upload", status=204)
    responses.post(
        "https://disk.pku.edu.cn/api/efast/v1/file/osendupload",
        json={"docid": DEFAULT_ROOT_ID + "/NEW", "rev": "REV1", "name": "result.bin"},
    )
    item = AnyShareClient("test-token").upload(local, "/")
    assert item.name == "result.bin"
    assert item.size == 6


@responses.activate
def test_download_uses_signed_request_and_atomic_destination(tmp_path: Path) -> None:
    client = AnyShareClient("test-token")
    client.resolve = lambda *_args, **_kwargs: type(
        "Item", (), {"id": "gns://file", "name": "model.bin", "kind": "file"}
    )()
    responses.post(
        "https://disk.pku.edu.cn/api/efast/v1/file/osdownload",
        json={"authrequest": ["GET", "https://objects.example/model", "X-Signed: yes"]},
    )
    responses.get("https://objects.example/model", body=b"weights")
    destination = client.download("/model.bin", tmp_path)
    assert destination.read_bytes() == b"weights"
    assert not (tmp_path / "model.bin.part").exists()
