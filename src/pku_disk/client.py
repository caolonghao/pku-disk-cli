from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

import requests
import truststore
from requests_toolbelt import MultipartEncoder

from . import __version__

truststore.inject_into_ssl()

DEFAULT_BASE_URL = "https://disk.pku.edu.cn"
DEFAULT_ROOT_ID = "gns://43FC0471AD1C458B91E339426F7909C4"


class AnyShareError(RuntimeError):
    pass


@dataclass(frozen=True)
class Entry:
    name: str
    id: str
    kind: str
    size: int
    modified_at: str | None = None
    rev: str | None = None

    @classmethod
    def from_api(cls, value: dict[str, Any], kind: str) -> Entry:
        return cls(
            name=value["name"],
            id=value["id"],
            kind=kind,
            size=value.get("size", -1),
            modified_at=value.get("modified_at"),
            rev=value.get("rev"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "type": self.kind,
            "size": self.size,
            "modified_at": self.modified_at,
            "rev": self.rev,
        }


@dataclass(frozen=True)
class SearchResult:
    name: str
    id: str
    kind: str
    size: int
    path: str
    parent_path: str
    modified_at: int | None = None

    @classmethod
    def from_api(cls, value: dict[str, Any]) -> SearchResult:
        name = value.get("basename", "") + value.get("extension", "")
        parent_path = value.get("parent_path", "")
        relative_parent = parent_path.removeprefix("gns://").partition("/")[2]
        path = "/" + "/".join(part for part in (relative_parent, name) if part)
        size = value.get("size", -1)
        return cls(
            name=name,
            id=value.get("doc_id", ""),
            kind="dir" if size < 0 else "file",
            size=size,
            path=path,
            parent_path=parent_path,
            modified_at=value.get("modified_at"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "id": self.id,
            "type": self.kind,
            "size": self.size,
            "path": self.path,
            "parent_path": self.parent_path,
            "modified_at": self.modified_at,
        }


class AnyShareClient:
    def __init__(
        self,
        token: str,
        base_url: str = DEFAULT_BASE_URL,
        root_id: str = DEFAULT_ROOT_ID,
        timeout: int = 60,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.root_id = root_id
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "X-Language": "zh-cn",
                "User-Agent": f"pku-disk-cli/{__version__}",
            }
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        timeout = kwargs.pop("timeout", self.timeout)
        response = self.session.request(
            method, self.base_url + path, timeout=timeout, **kwargs
        )
        if response.status_code == 401:
            raise AnyShareError("Authentication expired. Run: pku-disk auth login")
        if not response.ok:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text[:500]
            raise AnyShareError(
                f"AnyShare API returned HTTP {response.status_code}: {detail}"
            )
        return response

    def list_id(self, folder_id: str) -> list[Entry]:
        entries: list[Entry] = []
        marker = ""
        while True:
            encoded = quote(folder_id, safe="")
            params: dict[str, Any] = {
                "limit": 100,
                "sort": "name",
                "direction": "asc",
                "permission_attributes_required": "false",
            }
            if marker:
                params["marker"] = marker
            data = self._request(
                "GET", f"/api/efast/v1/folders/{encoded}/sub_objects", params=params
            ).json()
            entries.extend(Entry.from_api(item, "dir") for item in data.get("dirs", []))
            entries.extend(
                Entry.from_api(item, "file") for item in data.get("files", [])
            )
            marker = data.get("next_marker", "")
            if not marker:
                return sorted(
                    entries, key=lambda item: (item.kind != "dir", item.name.casefold())
                )

    def resolve(self, remote_path: str, expected: str | None = None) -> Entry:
        normalized = PurePosixPath("/" + remote_path.lstrip("/"))
        current = Entry("/", self.root_id, "dir", -1)
        for part in normalized.parts[1:]:
            match = next(
                (item for item in self.list_id(current.id) if item.name == part), None
            )
            if match is None:
                raise AnyShareError(f"Remote path not found: {remote_path}")
            current = match
        if expected and current.kind != expected:
            raise AnyShareError(
                f"Expected {expected}, found {current.kind}: {remote_path}"
            )
        return current

    def list_path(self, remote_path: str) -> list[Entry]:
        return self.list_id(self.resolve(remote_path, "dir").id)

    @staticmethod
    def _operation_endpoint(entry: Entry, operation: str) -> str:
        item_type = "dir" if entry.kind == "dir" else "file"
        return f"/api/efast/v1/{item_type}/{operation}"

    @staticmethod
    def _validate_name(name: str) -> None:
        if not name or len(name) > 255 or name.endswith("."):
            raise AnyShareError(
                "Remote name must be 1-255 characters and not end in '.'"
            )
        if any(character in name for character in '?^*|<>:\\/"'):
            raise AnyShareError("Remote name contains an unsupported character")

    def quota(self) -> dict[str, int | float]:
        data = self._request("GET", "/api/efast/v1/quota/user").json()
        allocated = int(data.get("allocated", 0))
        used = int(data.get("used", 0))
        return {
            "allocated": allocated,
            "used": used,
            "available": max(allocated - used, 0),
            "percent_used": round(used / allocated * 100, 2) if allocated else 0,
        }

    def search(
        self, query: str, limit: int = 50, item_type: str = "all"
    ) -> list[SearchResult]:
        if not query.strip():
            raise AnyShareError("Search query cannot be empty")
        if limit < 1:
            raise AnyShareError("Search limit must be positive")
        dimensions = {
            "all": ["basename"],
            "file": ["file", "basename"],
            "dir": ["folder", "basename"],
        }
        if item_type not in dimensions:
            raise AnyShareError(f"Unsupported search type: {item_type}")
        results: list[SearchResult] = []
        start = 0
        while len(results) < limit:
            rows = min(100, limit - len(results))
            data = self._request(
                "POST",
                "/api/ecosearch/v1/file-search",
                json={
                    "quick_search": True,
                    "dimension": dimensions[item_type],
                    "keyword": query.strip(),
                    "rows": rows,
                    "start": start,
                    "type": "doc",
                },
            ).json()
            page = [SearchResult.from_api(item) for item in data.get("files", [])]
            results.extend(page)
            next_start = data.get("next")
            if not page or next_start is None or next_start <= start:
                break
            start = next_start
        return results[:limit]

    def rename(self, remote_path: str, new_name: str) -> Entry:
        self._validate_name(new_name)
        entry = self.resolve(remote_path)
        if entry.id == self.root_id:
            raise AnyShareError("Refusing to rename the root library")
        self._request(
            "POST",
            self._operation_endpoint(entry, "rename"),
            json={"docid": entry.id, "name": new_name, "ondup": 1},
        )
        parent = PurePosixPath("/" + remote_path.lstrip("/")).parent
        return self.resolve(str(parent / new_name))

    def move(
        self, remote_path: str, destination_dir: str, rename_on_conflict: bool = False
    ) -> Entry:
        return self._relocate(remote_path, destination_dir, "move", rename_on_conflict)

    def copy(
        self, remote_path: str, destination_dir: str, rename_on_conflict: bool = False
    ) -> Entry:
        entry = self.resolve(remote_path)
        if entry.kind == "dir":
            return self._copy_directory(entry, destination_dir, rename_on_conflict)
        return self._relocate(remote_path, destination_dir, "copy", rename_on_conflict)

    def _copy_directory(
        self, source: Entry, destination_dir: str, rename_on_conflict: bool
    ) -> Entry:
        if source.id == self.root_id:
            raise AnyShareError("Refusing to copy the root library")
        destination = self.resolve(destination_dir, "dir")
        existing_names = {item.name for item in self.list_id(destination.id)}
        target_name = source.name
        if target_name in existing_names:
            if not rename_on_conflict:
                raise AnyShareError(
                    f"Remote item already exists: {destination_dir.rstrip('/')}/{target_name}"
                )
            index = 1
            while f"{source.name} ({index})" in existing_names:
                index += 1
            target_name = f"{source.name} ({index})"
        target_path = str(
            PurePosixPath("/" + destination_dir.lstrip("/")) / target_name
        )
        target = self.mkdir(target_path)

        def copy_contents(
            source_dir: Entry, target_dir: Entry, target_dir_path: str
        ) -> None:
            for item in self.list_id(source_dir.id):
                if item.kind == "dir":
                    child_path = str(PurePosixPath(target_dir_path) / item.name)
                    child_target = self.mkdir(child_path)
                    copy_contents(item, child_target, child_path)
                    continue
                self._request(
                    "POST",
                    "/api/efast/v1/file/copy",
                    json={"docid": item.id, "destparent": target_dir.id, "ondup": 1},
                )

        copy_contents(source, target, target_path)
        return self.resolve(target_path, "dir")

    def _relocate(
        self,
        remote_path: str,
        destination_dir: str,
        operation: str,
        rename_on_conflict: bool,
    ) -> Entry:
        entry = self.resolve(remote_path)
        if entry.id == self.root_id:
            raise AnyShareError(f"Refusing to {operation} the root library")
        destination = self.resolve(destination_dir, "dir")
        payload: dict[str, Any] = {
            "docid": entry.id,
            "destparent": destination.id,
            "ondup": 2 if rename_on_conflict else 1,
        }
        if entry.kind == "dir":
            payload["check_upload_process"] = True
        data = self._request(
            "POST", self._operation_endpoint(entry, operation), json=payload
        ).json()
        if isinstance(data, dict) and (data.get("docid") or data.get("id")):
            return Entry(
                name=data.get("name", entry.name),
                id=data.get("docid", data.get("id", "")),
                kind=entry.kind,
                size=data.get("size", entry.size),
                modified_at=data.get("modified_at", entry.modified_at),
                rev=data.get("rev", entry.rev),
            )
        return self.resolve(
            str(PurePosixPath("/" + destination_dir.lstrip("/")) / entry.name)
        )

    def remove(self, remote_path: str) -> dict[str, Any]:
        entry = self.resolve(remote_path)
        if entry.id == self.root_id:
            raise AnyShareError("Refusing to remove the root library")
        payload: dict[str, Any] = {"docid": entry.id}
        if entry.kind == "dir":
            payload["check_upload_process"] = True
        response = self._request(
            "POST", self._operation_endpoint(entry, "delete"), json=payload
        )
        return {
            "path": remote_path,
            "id": entry.id,
            "type": entry.kind,
            "removed": response.status_code != 202,
            "status": "pending_review" if response.status_code == 202 else "recycled",
        }

    def list_trash(self) -> dict[str, Any]:
        retention = self._request(
            "POST",
            "/api/efast/v1/recycle/getretentiondays",
            json={"docid": self.root_id},
        ).json()
        start = 0
        limit = 100
        entries: list[dict[str, Any]] = []
        server_time = 0
        while True:
            data = self._request(
                "POST",
                "/api/efast/v1/recycle/list",
                json={"docid": self.root_id, "start": start, "limit": limit},
            ).json()
            page: list[dict[str, Any]] = []
            for kind, values in (
                ("dir", data.get("dirs", [])),
                ("file", data.get("files", [])),
            ):
                page.extend(
                    {
                        "id": item.get("docid", ""),
                        "name": item.get("name", ""),
                        "type": kind,
                        "size": item.get("size", -1),
                        "original_path": item.get("path", ""),
                        "deleted_by": item.get("editor", ""),
                        "deleted_at": item.get("modified"),
                    }
                    for item in values
                )
            entries.extend(page)
            server_time = data.get("servertime", server_time)
            if len(page) < limit:
                break
            start += len(page)
        return {
            "retention_days": retention.get("days", -1),
            "server_time": server_time,
            "entries": entries,
        }

    def restore_trash(
        self, item_id: str, rename_on_conflict: bool = False
    ) -> dict[str, Any]:
        data = self._request(
            "POST",
            "/api/efast/v1/recycle/restore",
            json={"docid": item_id, "ondup": 2 if rename_on_conflict else 1},
        ).json()
        return {"id": item_id, "restored": True, "result": data}

    def delete_trash(self, item_id: str) -> dict[str, Any]:
        self._request("POST", "/api/efast/v1/recycle/delete", json={"docid": item_id})
        return {"id": item_id, "deleted": True}

    def mkdir(self, remote_path: str, parents: bool = False) -> Entry:
        path = PurePosixPath("/" + remote_path.lstrip("/"))
        if path == PurePosixPath("/"):
            return Entry("/", self.root_id, "dir", -1)
        current = Entry("/", self.root_id, "dir", -1)
        for index, part in enumerate(path.parts[1:]):
            match = next(
                (item for item in self.list_id(current.id) if item.name == part), None
            )
            is_last = index == len(path.parts[1:]) - 1
            if match:
                if match.kind != "dir":
                    raise AnyShareError(
                        f"A file blocks the requested directory: {part}"
                    )
                if is_last:
                    raise AnyShareError(f"Directory already exists: {remote_path}")
                current = match
                continue
            if not parents and not is_last:
                raise AnyShareError(f"Parent directory does not exist: {part}")
            data = self._request(
                "POST",
                "/api/efast/v1/dir/create",
                json={"docid": current.id, "name": part, "ondup": 1},
            ).json()
            current = Entry(
                name=data.get("name", part),
                id=data.get("id", data.get("docid", "")),
                kind="dir",
                size=-1,
                modified_at=data.get("modified_at"),
                rev=data.get("rev"),
            )
            if not current.id:
                raise AnyShareError("Directory creation response did not include an ID")
        return current

    def upload(
        self, local_path: Path, remote_dir: str, rename_on_conflict: bool = False
    ) -> Entry:
        local_path = local_path.expanduser().resolve()
        if not local_path.is_file():
            raise AnyShareError(f"Local file not found: {local_path}")
        parent = self.resolve(remote_dir, "dir")
        existing = next(
            (item for item in self.list_id(parent.id) if item.name == local_path.name),
            None,
        )
        if existing and not rename_on_conflict:
            raise AnyShareError(
                f"Remote item already exists: {remote_dir.rstrip('/')}/{local_path.name}. "
                "Use --rename-on-conflict to keep both files."
            )

        payload = {
            "client_mtime": int(local_path.stat().st_mtime * 1000),
            "docid": parent.id,
            "length": local_path.stat().st_size,
            "name": local_path.name,
            "ondup": 2 if rename_on_conflict else 1,
            "reqmethod": "POST",
        }
        begin = self._request(
            "POST", "/api/efast/v1/file/osbeginupload", json=payload
        ).json()
        auth_request = begin["authrequest"]
        method, upload_url, *fields = auth_request
        form: dict[str, str] = {}
        for field in fields:
            key, value = field.split(": ", 1)
            form[key] = value

        with local_path.open("rb") as stream:
            multipart = MultipartEncoder(
                fields={
                    **form,
                    "file": (local_path.name, stream, "application/octet-stream"),
                }
            )
            response = requests.request(
                method,
                upload_url,
                data=multipart,
                headers={"Content-Type": multipart.content_type},
                timeout=None,
            )
        if not response.ok:
            raise AnyShareError(
                f"Object storage upload returned HTTP {response.status_code}"
            )

        completed = self._request(
            "POST",
            "/api/efast/v1/file/osendupload",
            json={"docid": begin["docid"], "rev": begin["rev"]},
        ).json()
        return Entry(
            name=completed.get("name", local_path.name),
            id=completed.get("docid", begin["docid"]),
            kind="file",
            size=local_path.stat().st_size,
            rev=completed.get("rev", begin["rev"]),
        )

    def download(self, remote_path: str, destination: Path) -> Path:
        entry = self.resolve(remote_path, "file")
        destination = destination.expanduser().resolve()
        if destination.is_dir():
            destination = destination / entry.name
        if destination.exists():
            raise AnyShareError(f"Local destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)

        data = self._request(
            "POST", "/api/efast/v1/file/osdownload", json={"docid": entry.id}
        ).json()
        method, download_url, *header_lines = data["authrequest"]
        headers: dict[str, str] = {}
        for line in header_lines:
            key, value = line.split(": ", 1)
            headers[key] = value

        temporary = destination.with_name(destination.name + ".part")
        try:
            with requests.request(
                method, download_url, headers=headers, stream=True, timeout=None
            ) as response:
                if not response.ok:
                    raise AnyShareError(
                        f"Object storage download returned HTTP {response.status_code}"
                    )
                with temporary.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
                        if chunk:
                            output.write(chunk)
            os.replace(temporary, destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return destination

    def iter_tree(self, remote_path: str) -> Iterator[tuple[str, Entry]]:
        root = self.resolve(remote_path, "dir")

        def walk(folder: Entry, prefix: str) -> Iterator[tuple[str, Entry]]:
            for item in self.list_id(folder.id):
                relative = f"{prefix}/{item.name}" if prefix else item.name
                yield relative, item
                if item.kind == "dir":
                    yield from walk(item, relative)

        yield from walk(root, "")
