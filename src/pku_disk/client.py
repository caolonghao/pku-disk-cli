from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

import requests
from requests_toolbelt import MultipartEncoder

from . import __version__

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
            current = Entry.from_api(data, "dir")
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
