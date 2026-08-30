import json
from pathlib import Path

import pytest
import responses

from pku_disk.client import AnyShareClient, AnyShareError, Entry
from pku_disk.transfers import TransferClient


def request_json(index: int) -> dict:
    return json.loads(responses.calls[index].request.body)


@responses.activate
def test_multipart_upload_completes_protocol(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    local = tmp_path / "large.bin"
    local.write_bytes(b"abcdefghij")
    client = AnyShareClient("test-token")
    client.resolve = lambda *_args, **_kwargs: Entry("dest", "gns://dest", "dir", -1)
    client.list_id = lambda *_args: []
    responses.post(
        "https://disk.pku.edu.cn/api/efast/v1/file/osinitmultiupload",
        json={"uploadid": "UPLOAD", "docid": "gns://new", "rev": "REV"},
    )
    responses.post(
        "https://disk.pku.edu.cn/api/efast/v1/file/osuploadpart",
        json={
            "authrequests": {
                "1": ["PUT", "https://objects.example/1", "X-Test: yes"],
                "2": ["PUT", "https://objects.example/2", "X-Test: yes"],
                "3": ["PUT", "https://objects.example/3", "X-Test: yes"],
            }
        },
    )
    for number in range(1, 4):
        responses.put(
            f"https://objects.example/{number}",
            headers={"ETag": f"etag-{number}"},
        )
    completion = (
        "--BOUNDARY\nconfirm-body\n--BOUNDARY\n"
        '{"authrequest":["POST","https://objects.example/confirm","X-Test: yes"]}'
        "\n--BOUNDARY--"
    )
    responses.post(
        "https://disk.pku.edu.cn/api/efast/v1/file/oscompleteupload",
        json=completion,
    )
    responses.post("https://objects.example/confirm")
    responses.post(
        "https://disk.pku.edu.cn/api/efast/v1/file/osendupload",
        json={"docid": "gns://new", "rev": "REV", "name": "large.bin"},
    )

    entry = TransferClient(client, chunk_size=4, multipart_threshold=1).upload_file(
        local, "/dest"
    )
    assert entry.id == "gns://new"
    part_info = request_json(5)["partinfo"]
    assert part_info == {
        "1": ["etag-1", 4],
        "2": ["etag-2", 4],
        "3": ["etag-3", 2],
    }
    assert not list((tmp_path / "state").rglob("*.json"))


@responses.activate
def test_multipart_upload_resumes_completed_parts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    local = tmp_path / "resume.bin"
    local.write_bytes(b"abcdefgh")
    client = AnyShareClient("test-token")
    client.resolve = lambda *_args, **_kwargs: Entry("dest", "gns://dest", "dir", -1)
    client.list_id = lambda *_args: []
    init_url = "https://disk.pku.edu.cn/api/efast/v1/file/osinitmultiupload"
    sign_url = "https://disk.pku.edu.cn/api/efast/v1/file/osuploadpart"
    responses.post(
        init_url, json={"uploadid": "UPLOAD", "docid": "gns://new", "rev": "REV"}
    )
    signed = {
        "authrequests": {
            "1": ["PUT", "https://objects.example/1"],
            "2": ["PUT", "https://objects.example/2"],
        }
    }
    responses.post(sign_url, json=signed)
    responses.put("https://objects.example/1", headers={"ETag": "etag-1"})
    responses.put("https://objects.example/2", status=500)
    transfer = TransferClient(client, chunk_size=4, multipart_threshold=1)
    with pytest.raises(AnyShareError, match="part 2"):
        transfer.upload_file(local, "/dest")

    responses.post(sign_url, json=signed)
    responses.put("https://objects.example/2", headers={"ETag": "etag-2"})
    completion = (
        "--BOUNDARY\nbody\n--BOUNDARY\n"
        '{"authrequest":["POST","https://objects.example/confirm"]}'
        "\n--BOUNDARY--"
    )
    responses.post(
        "https://disk.pku.edu.cn/api/efast/v1/file/oscompleteupload", json=completion
    )
    responses.post("https://objects.example/confirm")
    responses.post(
        "https://disk.pku.edu.cn/api/efast/v1/file/osendupload",
        json={"docid": "gns://new", "rev": "REV"},
    )
    assert transfer.upload_file(local, "/dest").id == "gns://new"
    first_part_calls = [
        call
        for call in responses.calls
        if call.request.url == "https://objects.example/1"
    ]
    assert len(first_part_calls) == 1
