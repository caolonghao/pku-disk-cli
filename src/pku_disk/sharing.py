from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from .client import AnyShareClient, AnyShareError, Entry

PERMISSION_CHOICES = ("preview", "download", "upload")
ACCESSOR_TYPES = ("user", "department", "group", "contactor")


def permission_api_values(permissions: list[str]) -> list[str]:
    invalid = sorted(set(permissions) - set(PERMISSION_CHOICES))
    if invalid:
        raise AnyShareError(f"Unsupported share permissions: {', '.join(invalid)}")
    values = {"display"}
    if "preview" in permissions:
        values.add("preview")
    if "download" in permissions:
        values.add("download")
    if "upload" in permissions:
        values.update(("create", "modify"))
    order = ("display", "preview", "download", "create", "modify")
    return [value for value in order if value in values]


def api_permission_names(values: list[str]) -> list[str]:
    permissions: list[str] = []
    if "preview" in values:
        permissions.append("preview")
    if "download" in values:
        permissions.append("download")
    if "create" in values or "modify" in values:
        permissions.append("upload")
    return permissions


def expiry_iso(days: int) -> str:
    if days < 0:
        raise AnyShareError("Expiry days cannot be negative")
    if days == 0:
        return "1970-01-01T00:00:00Z"
    value = datetime.now(timezone.utc) + timedelta(days=days)
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def expiry_milliseconds(days: int) -> int:
    if days < 0:
        raise AnyShareError("Expiry days cannot be negative")
    if days == 0:
        return -1
    return int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp() * 1000)


class SharingClient:
    def __init__(self, client: AnyShareClient) -> None:
        self.client = client

    @staticmethod
    def _item_type(entry: Entry) -> str:
        return "folder" if entry.kind == "dir" else "file"

    @classmethod
    def _redact_secrets(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [cls._redact_secrets(item) for item in value]
        if not isinstance(value, dict):
            return value
        output = {
            key: cls._redact_secrets(item)
            for key, item in value.items()
            if key != "password"
        }
        if "password" in value:
            output["password_set"] = bool(value["password"])
        return output

    def policy(self) -> dict[str, Any]:
        config = self.client._request(
            "POST", "/api/eacp/v1/perm1/getsharedocconfig", json={}
        ).json()
        anonymous = self.client._request(
            "GET", "/api/doc-share/v1/sharing-configuration-scope/anyone"
        ).json()
        realname = self.client._request(
            "GET", "/api/doc-share/v1/sharing-configuration-scope/user"
        ).json()
        return {"features": config, "anonymous": anonymous, "realname": realname}

    def list_links(self, remote_path: str) -> list[dict[str, Any]]:
        entry = self.client.resolve(remote_path)
        encoded = quote(entry.id, safe="")
        data = self.client._request(
            "GET",
            f"/api/shared-link/v1/document/{self._item_type(entry)}/{encoded}",
            params={"type": "anonymous"},
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        ).json()
        return [self._link_output(item, remote_path) for item in data]

    def list_all(self, share_type: str = "all") -> dict[str, Any]:
        output: dict[str, Any] = {}
        if share_type in ("all", "anonymous"):
            output["anonymous"] = self._list_managed(
                "/api/doc-share/v1/docs-shared-with-anyone"
            )
        if share_type in ("all", "realname"):
            output["realname"] = self._list_managed(
                "/api/doc-share/v1/docs-shared-with-users"
            )
        return output

    def _list_managed(self, endpoint: str) -> list[dict[str, Any]]:
        offset = 0
        limit = 100
        entries: list[dict[str, Any]] = []
        while True:
            data = self.client._request(
                "GET", endpoint, params={"offset": offset, "limit": limit}
            ).json()
            page = data.get("entries", [])
            entries.extend(page)
            offset += len(page)
            if not page or offset >= data.get("total_count", offset):
                return self._redact_secrets(entries)

    def create_link(
        self,
        remote_path: str,
        permissions: list[str],
        expires_days: int,
        title: str | None = None,
        password: str = "",
        max_uses: int = -1,
    ) -> dict[str, Any]:
        if max_uses < -1:
            raise AnyShareError("Maximum uses must be -1 (unlimited) or non-negative")
        entry = self.client.resolve(remote_path)
        payload = {
            "item": {
                "id": entry.id,
                "type": self._item_type(entry),
                "allow": permission_api_values(permissions),
            },
            "title": title or entry.name,
            "expires_at": expiry_iso(expires_days),
            "password": password,
            "verify_mobile": False,
            "limited_times": max_uses,
        }
        response = self.client._request(
            "POST", "/api/shared-link/v1/document/anonymous", json=payload
        )
        data = response.json()
        if response.status_code == 202:
            data["status"] = "pending_review"
        return self._link_output(data, remote_path)

    def get_link(self, link_id: str) -> dict[str, Any]:
        return self._link_output(self._get_link_raw(link_id))

    def _get_link_raw(self, link_id: str) -> dict[str, Any]:
        encoded = quote(link_id, safe="")
        return self.client._request(
            "GET", f"/api/shared-link/v1/links/{encoded}"
        ).json()

    def update_link(
        self,
        link_id: str,
        permissions: list[str] | None = None,
        expires_days: int | None = None,
        title: str | None = None,
        password: str | None = None,
        max_uses: int | None = None,
    ) -> dict[str, Any]:
        if max_uses is not None and max_uses < -1:
            raise AnyShareError("Maximum uses must be -1 (unlimited) or non-negative")
        current = self._get_link_raw(link_id)
        current_item = current.get("item", {})
        allow = (
            permission_api_values(permissions)
            if permissions is not None
            else current_item.get("allow", [])
        )
        payload = {
            "item": {"allow": allow},
            "title": title if title is not None else current.get("title", ""),
            "expires_at": (
                expiry_iso(expires_days)
                if expires_days is not None
                else current.get("expires_at")
            ),
            "password": password
            if password is not None
            else current.get("password", ""),
            "verify_mobile": current.get("verify_mobile", False),
            "limited_times": (
                max_uses if max_uses is not None else current.get("limited_times", -1)
            ),
        }
        encoded = quote(link_id, safe="")
        response = self.client._request(
            "PUT",
            f"/api/shared-link/v1/document/anonymous/{encoded}",
            json=payload,
        )
        if response.status_code == 202:
            return {"id": link_id, "status": "pending_review"}
        return self.get_link(link_id)

    def revoke_link(self, link_id: str) -> dict[str, Any]:
        encoded = quote(link_id, safe="")
        self.client._request(
            "DELETE", f"/api/shared-link/v1/document/anonymous/{encoded}"
        )
        return {"id": link_id, "revoked": True}

    def _link_output(
        self, value: dict[str, Any], remote_path: str | None = None
    ) -> dict[str, Any]:
        output = self._redact_secrets(value)
        link_id = output.get("id")
        if link_id:
            output["url"] = f"{self.client.base_url}/anyshare/zh-cn/link/{link_id}"
        if remote_path is not None:
            output["path"] = remote_path
        item = output.get("item")
        if isinstance(item, dict) and "allow" in item:
            output["permissions"] = api_permission_names(item.get("allow", []))
        return output

    def search_accessors(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        if not query.strip():
            raise AnyShareError("Accessor search query cannot be empty")
        if limit < 1:
            raise AnyShareError("Accessor search limit must be positive")
        payload = {"key": query, "limit": limit, "start": 0}
        department = self.client._request(
            "POST", "/api/eacp/v1/department/search", json=payload
        ).json()
        contactor = self.client._request(
            "POST", "/api/eacp/v1/contactor/search", json=payload
        ).json()
        group = self.client._request(
            "GET",
            "/api/user-management/v1/search-in-group",
            params={
                "keyword": query,
                "type": ["group", "member"],
                "offset": 0,
                "limit": limit,
            },
        ).json()

        candidates: list[dict[str, Any]] = []
        for item in department.get("userinfos", []) + contactor.get("userinfos", []):
            candidates.append(self._accessor_output(item, "user"))
        for item in department.get("depinfos", []):
            candidates.append(self._accessor_output(item, "department"))
        for item in contactor.get("groups", []):
            candidates.append(self._accessor_output(item, "contactor"))
        for item in group.get("groups", {}).get("entries", []):
            candidates.append(self._accessor_output(item, "group"))
        for item in group.get("members", {}).get("entries", []):
            kind = "department" if item.get("type") == "department" else "user"
            candidates.append(self._accessor_output(item, kind))

        unique: dict[tuple[str, str], dict[str, Any]] = {}
        for item in candidates:
            if item["id"]:
                unique[(item["type"], item["id"])] = item
        return list(unique.values())[:limit]

    @staticmethod
    def _accessor_output(value: dict[str, Any], kind: str) -> dict[str, Any]:
        identifier = value.get("userid") or value.get("depid") or value.get("id", "")
        return {
            "id": identifier,
            "type": kind,
            "name": value.get("name") or value.get("groupname", ""),
            "account": value.get("account", ""),
            "path": value.get("deppath") or value.get("path", ""),
        }

    def list_grants(self, remote_path: str) -> dict[str, Any]:
        entry = self.client.resolve(remote_path)
        data = self.client._request(
            "POST", "/api/eacp/v1/perm2/get", json={"docid": entry.id}
        ).json()
        return {"path": remote_path, **data}

    def grant(
        self,
        remote_path: str,
        accessor_id: str,
        accessor_type: str,
        permissions: list[str],
        expires_days: int,
        accessor_name: str = "",
    ) -> dict[str, Any]:
        if accessor_type not in ACCESSOR_TYPES:
            raise AnyShareError(f"Unsupported accessor type: {accessor_type}")
        entry = self.client.resolve(remote_path)
        acl = self.client._request(
            "POST", "/api/eacp/v1/perm2/get", json={"docid": entry.id}
        ).json()
        grants = list(acl.get("perminfos", []))
        replacement = {
            "accessorid": accessor_id,
            "accessortype": accessor_type,
            "accessorname": accessor_name,
            "allow": permission_api_values(permissions),
            "deny": [],
            "endtime": expiry_milliseconds(expires_days),
        }
        replaced = False
        for index, current in enumerate(grants):
            if (
                current.get("accessorid") == accessor_id
                and current.get("accessortype") == accessor_type
                and not current.get("inheritdocid")
            ):
                grants[index] = replacement
                replaced = True
        if not replaced:
            grants.append(replacement)
        result = self.client._request(
            "POST",
            "/api/eacp/v1/perm2/set",
            json={
                "docid": entry.id,
                "perminfos": grants,
                "inherit": acl.get("inherit", True),
            },
        ).json()
        return {"path": remote_path, "grant": replacement, "result": result}

    def revoke_grant(
        self, remote_path: str, accessor_id: str, accessor_type: str
    ) -> dict[str, Any]:
        entry = self.client.resolve(remote_path)
        acl = self.client._request(
            "POST", "/api/eacp/v1/perm2/get", json={"docid": entry.id}
        ).json()
        before = list(acl.get("perminfos", []))
        after = [
            item
            for item in before
            if item.get("inheritdocid")
            or item.get("accessorid") != accessor_id
            or item.get("accessortype") != accessor_type
        ]
        if len(after) == len(before):
            raise AnyShareError("Matching direct grant not found")
        result = self.client._request(
            "POST",
            "/api/eacp/v1/perm2/set",
            json={
                "docid": entry.id,
                "perminfos": after,
                "inherit": acl.get("inherit", True),
            },
        ).json()
        return {
            "path": remote_path,
            "accessor_id": accessor_id,
            "accessor_type": accessor_type,
            "revoked": True,
            "result": result,
        }
