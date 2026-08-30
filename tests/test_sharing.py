import json

import pytest
import responses

from pku_disk.cli import build_parser
from pku_disk.client import AnyShareClient, AnyShareError, Entry
from pku_disk.sharing import SharingClient, permission_api_values


def sharing_client(entry: Entry | None = None) -> SharingClient:
    client = AnyShareClient("test-token")
    resolved = entry or Entry("report.pdf", "gns://folder/report", "file", 42)
    client.resolve = lambda *_args, **_kwargs: resolved
    return SharingClient(client)


def request_json(call_index: int) -> dict:
    return json.loads(responses.calls[call_index].request.body)


def test_permission_mapping_and_validation() -> None:
    assert permission_api_values(["preview", "upload"]) == [
        "display",
        "preview",
        "create",
        "modify",
    ]
    with pytest.raises(AnyShareError, match="Unsupported"):
        permission_api_values(["owner"])


def test_cli_permission_defaults_do_not_mix_with_explicit_values() -> None:
    parser = build_parser()
    defaults = parser.parse_args(["share", "link", "create", "/report.pdf"])
    explicit = parser.parse_args(
        ["share", "link", "create", "/report.pdf", "--permission", "preview"]
    )
    assert defaults.permissions is None
    assert explicit.permissions == ["preview"]


@responses.activate
def test_create_link_sends_expected_payload_and_redacts_password() -> None:
    responses.post(
        "https://disk.pku.edu.cn/api/shared-link/v1/document/anonymous",
        json={
            "id": "LINK1",
            "password": "server-secret",
            "item": {"allow": ["display", "preview"]},
        },
    )
    result = sharing_client().create_link(
        "/report.pdf", ["preview"], 0, password="client-secret", max_uses=3
    )

    payload = request_json(0)
    assert payload["item"] == {
        "id": "gns://folder/report",
        "type": "file",
        "allow": ["display", "preview"],
    }
    assert payload["password"] == "client-secret"
    assert payload["limited_times"] == 3
    assert "password" not in result
    assert result["password_set"] is True
    assert result["url"].endswith("/anyshare/zh-cn/link/LINK1")


@responses.activate
def test_update_link_preserves_unspecified_fields_without_printing_password() -> None:
    url = "https://disk.pku.edu.cn/api/shared-link/v1/links/LINK1"
    responses.get(
        url,
        json={
            "id": "LINK1",
            "title": "Old title",
            "expires_at": "2030-01-01T00:00:00Z",
            "password": "keep-secret",
            "verify_mobile": False,
            "limited_times": 9,
            "item": {"allow": ["display", "preview", "download"]},
        },
    )
    responses.put(
        "https://disk.pku.edu.cn/api/shared-link/v1/document/anonymous/LINK1",
        json={},
    )
    responses.get(
        url,
        json={
            "id": "LINK1",
            "title": "New title",
            "password": "keep-secret",
            "item": {"allow": ["display", "preview", "download"]},
        },
    )

    result = sharing_client().update_link("LINK1", title="New title")
    payload = request_json(1)
    assert payload["title"] == "New title"
    assert payload["password"] == "keep-secret"
    assert payload["limited_times"] == 9
    assert "password" not in result
    assert result["password_set"] is True


@responses.activate
def test_grant_replaces_only_matching_direct_entry() -> None:
    inherited = {
        "accessorid": "USER1",
        "accessortype": "user",
        "inheritdocid": "PARENT",
        "allow": ["display"],
    }
    existing = {
        "accessorid": "USER1",
        "accessortype": "user",
        "allow": ["display"],
    }
    other = {
        "accessorid": "USER2",
        "accessortype": "user",
        "allow": ["display", "preview"],
    }
    responses.post(
        "https://disk.pku.edu.cn/api/eacp/v1/perm2/get",
        json={"inherit": True, "perminfos": [inherited, existing, other]},
    )
    responses.post(
        "https://disk.pku.edu.cn/api/eacp/v1/perm2/set", json={"success": True}
    )

    sharing_client().grant(
        "/report.pdf", "USER1", "user", ["preview", "download"], 0, "Alice"
    )
    payload = request_json(1)
    assert payload["inherit"] is True
    assert payload["perminfos"][0] == inherited
    assert payload["perminfos"][2] == other
    assert payload["perminfos"][1]["accessorname"] == "Alice"
    assert payload["perminfos"][1]["allow"] == [
        "display",
        "preview",
        "download",
    ]


@responses.activate
def test_revoke_grant_preserves_inherited_and_unrelated_entries() -> None:
    inherited = {
        "accessorid": "USER1",
        "accessortype": "user",
        "inheritdocid": "PARENT",
    }
    direct = {"accessorid": "USER1", "accessortype": "user"}
    other = {"accessorid": "DEPT1", "accessortype": "department"}
    responses.post(
        "https://disk.pku.edu.cn/api/eacp/v1/perm2/get",
        json={"inherit": False, "perminfos": [inherited, direct, other]},
    )
    responses.post(
        "https://disk.pku.edu.cn/api/eacp/v1/perm2/set", json={"success": True}
    )

    result = sharing_client().revoke_grant("/report.pdf", "USER1", "user")
    payload = request_json(1)
    assert payload == {
        "docid": "gns://folder/report",
        "perminfos": [inherited, other],
        "inherit": False,
    }
    assert result["revoked"] is True


def test_invalid_limits_are_rejected_before_network_access() -> None:
    with pytest.raises(AnyShareError, match="Maximum uses"):
        sharing_client().create_link("/report.pdf", ["preview"], 30, max_uses=-2)
    with pytest.raises(AnyShareError, match="positive"):
        sharing_client().search_accessors("Alice", limit=0)
