from pathlib import Path

import pytest
import responses

from pku_disk.client import DEFAULT_ROOT_ID, AnyShareClient, AnyShareError, Entry


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


@responses.activate
def test_expired_token_refreshes_once_and_retries() -> None:
    url = "https://disk.pku.edu.cn/api/efast/v1/quota/user"
    responses.get(url, status=401)
    responses.get(url, json={"allocated": 100, "used": 25})
    refreshed: list[bool] = []

    def refresh() -> str:
        refreshed.append(True)
        return "fresh-token"

    client = AnyShareClient("expired", token_refresher=refresh)
    assert client.quota()["available"] == 75
    assert refreshed == [True]
    assert responses.calls[1].request.headers["Authorization"] == "Bearer fresh-token"


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
def test_mkdir_accepts_minimal_server_response() -> None:
    root_url = "https://disk.pku.edu.cn/api/efast/v1/folders/gns%3A%2F%2F43FC0471AD1C458B91E339426F7909C4/sub_objects"
    responses.get(root_url, json={"dirs": [], "files": [], "next_marker": ""})
    responses.post(
        "https://disk.pku.edu.cn/api/efast/v1/dir/create",
        json={"docid": DEFAULT_ROOT_ID + "/NEW", "rev": "REV"},
    )
    entry = AnyShareClient("test-token").mkdir("/new-folder")
    assert entry.name == "new-folder"
    assert entry.id == DEFAULT_ROOT_ID + "/NEW"


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


@responses.activate
def test_quota_adds_available_and_percentage() -> None:
    responses.get(
        "https://disk.pku.edu.cn/api/efast/v1/quota/user",
        json={"allocated": 1000, "used": 250},
    )
    assert AnyShareClient("test-token").quota() == {
        "allocated": 1000,
        "used": 250,
        "available": 750,
        "percent_used": 25.0,
    }


@responses.activate
def test_search_maps_results_and_paginates() -> None:
    url = "https://disk.pku.edu.cn/api/ecosearch/v1/file-search"
    responses.post(
        url,
        json={
            "files": [
                {
                    "basename": "report",
                    "extension": ".pdf",
                    "doc_id": "gns://file",
                    "parent_path": "gns://My Library/research",
                    "size": 42,
                    "modified_at": 123,
                }
            ],
            "next": 1,
        },
    )
    responses.post(url, json={"files": [], "next": 1})
    result = AnyShareClient("test-token").search("report", limit=2)
    assert result[0].as_dict() == {
        "name": "report.pdf",
        "id": "gns://file",
        "type": "file",
        "size": 42,
        "path": "/research/report.pdf",
        "parent_path": "gns://My Library/research",
        "modified_at": 123,
    }
    assert responses.calls[1].request.body is not None


@responses.activate
def test_rename_move_copy_and_remove_use_item_specific_endpoints() -> None:
    client = AnyShareClient("test-token")
    source = Entry("old.txt", "gns://root/OLD", "file", 4)
    destination = Entry("dest", "gns://root/DEST", "dir", -1)
    renamed = Entry("new.txt", source.id, "file", 4)
    copied = Entry("old.txt", "gns://root/DEST/COPY", "file", 4)
    resolved = iter(
        [
            source,
            renamed,
            source,
            destination,
            copied,
            source,
            source,
            destination,
            copied,
            source,
        ]
    )
    client.resolve = lambda *_args, **_kwargs: next(resolved)
    for endpoint in ("rename", "move", "copy", "delete"):
        responses.post(f"https://disk.pku.edu.cn/api/efast/v1/file/{endpoint}", json={})

    assert client.rename("/old.txt", "new.txt") == renamed
    assert client.move("/old.txt", "/dest") == copied
    assert client.copy("/old.txt", "/dest") == copied
    assert client.remove("/old.txt")["status"] == "recycled"


def test_root_cannot_be_renamed_or_removed() -> None:
    client = AnyShareClient("test-token")
    client.resolve = lambda *_args, **_kwargs: Entry("/", DEFAULT_ROOT_ID, "dir", -1)
    with pytest.raises(AnyShareError, match="root"):
        client.rename("/", "other")
    with pytest.raises(AnyShareError, match="root"):
        client.remove("/")


@responses.activate
def test_directory_copy_recursively_creates_dirs_and_copies_files() -> None:
    client = AnyShareClient("test-token")
    source = Entry("source", "gns://source", "dir", -1)
    destination = Entry("dest", "gns://dest", "dir", -1)
    child_dir = Entry("nested", "gns://nested", "dir", -1)
    top_file = Entry("top.txt", "gns://top", "file", 3)
    nested_file = Entry("nested.txt", "gns://nested-file", "file", 6)
    created = {
        "/dest/source": Entry("source", "gns://target", "dir", -1),
        "/dest/source/nested": Entry("nested", "gns://target-nested", "dir", -1),
    }

    def resolve(path: str, *_args: str) -> Entry:
        if path == "/source":
            return source
        if path == "/dest":
            return destination
        return created[path]

    client.resolve = resolve
    client.mkdir = lambda path, *_args: created[path]
    client.list_id = lambda item_id: {
        destination.id: [],
        source.id: [child_dir, top_file],
        child_dir.id: [nested_file],
    }[item_id]
    responses.post("https://disk.pku.edu.cn/api/efast/v1/file/copy", json={})
    responses.post("https://disk.pku.edu.cn/api/efast/v1/file/copy", json={})

    assert client.copy("/source", "/dest") == created["/dest/source"]
    copied_ids = [call.request.body for call in responses.calls]
    assert b"gns://top" in copied_ids[1]
    assert b"gns://nested-file" in copied_ids[0]


@responses.activate
def test_trash_list_restore_and_delete() -> None:
    responses.post(
        "https://disk.pku.edu.cn/api/efast/v1/recycle/getretentiondays",
        json={"days": 30},
    )
    responses.post(
        "https://disk.pku.edu.cn/api/efast/v1/recycle/list",
        json={
            "dirs": [],
            "files": [
                {
                    "docid": "gns://deleted",
                    "name": "old.txt",
                    "size": 4,
                    "path": "gns://Library/folder",
                    "editor": "user",
                    "modified": 123,
                }
            ],
            "servertime": 456,
        },
    )
    responses.post(
        "https://disk.pku.edu.cn/api/efast/v1/recycle/restore", json={"path": "/"}
    )
    responses.post("https://disk.pku.edu.cn/api/efast/v1/recycle/delete", json={})
    client = AnyShareClient("test-token")
    listing = client.list_trash()
    assert listing["retention_days"] == 30
    assert listing["entries"][0]["id"] == "gns://deleted"
    assert client.restore_trash("gns://deleted")["restored"] is True
    assert client.delete_trash("gns://deleted")["deleted"] is True
