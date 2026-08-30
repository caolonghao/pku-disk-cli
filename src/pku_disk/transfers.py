from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

import requests

from .client import AnyShareClient, AnyShareError, Entry

CHUNK_SIZE = 4 * 1024 * 1024
MULTIPART_THRESHOLD = 16 * 1024 * 1024
ProgressCallback = Callable[[int, int, str], None]


def _state_root() -> Path:
    root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return root / "pku-disk" / "uploads"


def _state_path(local_path: Path, parent_id: str) -> Path:
    stat = local_path.stat()
    identity = (
        f"{local_path.resolve()}\0{parent_id}\0{stat.st_size}\0{stat.st_mtime_ns}"
    )
    return _state_root() / (hashlib.sha256(identity.encode()).hexdigest() + ".json")


def _save_state(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def _headers(lines: list[str]) -> dict[str, str]:
    output: dict[str, str] = {}
    for line in lines:
        key, value = line.split(": ", 1)
        if key.lower() != "x-as-userid":
            output[key] = value
    return output


class TransferClient:
    def __init__(
        self,
        client: AnyShareClient,
        progress: ProgressCallback | None = None,
        chunk_size: int = CHUNK_SIZE,
        multipart_threshold: int = MULTIPART_THRESHOLD,
    ) -> None:
        self.client = client
        self.progress = progress
        self.chunk_size = chunk_size
        self.multipart_threshold = multipart_threshold

    def _progress(self, completed: int, total: int, label: str) -> None:
        if self.progress:
            self.progress(completed, total, label)

    def _ensure_remote_dir(self, remote_path: str) -> Entry:
        try:
            return self.client.resolve(remote_path, "dir")
        except AnyShareError as error:
            if "Remote path not found" not in str(error):
                raise
            return self.client.mkdir(remote_path)

    def upload(
        self, local_path: Path, remote_dir: str, rename_on_conflict: bool = False
    ) -> list[dict[str, Any]]:
        local_path = local_path.expanduser().resolve()
        if local_path.is_file():
            return [
                self.upload_file(local_path, remote_dir, rename_on_conflict).as_dict()
            ]
        if not local_path.is_dir():
            raise AnyShareError(f"Local path not found: {local_path}")
        destination = str(PurePosixPath("/" + remote_dir.lstrip("/")) / local_path.name)
        self._ensure_remote_dir(destination)
        uploaded: list[dict[str, Any]] = []
        for directory, dirnames, filenames in os.walk(local_path):
            dirnames.sort()
            filenames.sort()
            relative = Path(directory).relative_to(local_path)
            remote_current = str(PurePosixPath(destination) / relative.as_posix())
            for dirname in dirnames:
                self._ensure_remote_dir(str(PurePosixPath(remote_current) / dirname))
            for filename in filenames:
                entry = self.upload_file(
                    Path(directory) / filename,
                    remote_current,
                    rename_on_conflict,
                )
                uploaded.append(
                    {"local": str(Path(directory) / filename), **entry.as_dict()}
                )
        return uploaded

    def upload_file(
        self, local_path: Path, remote_dir: str, rename_on_conflict: bool = False
    ) -> Entry:
        if local_path.stat().st_size < self.multipart_threshold:
            entry = self.client.upload(local_path, remote_dir, rename_on_conflict)
            self._progress(
                local_path.stat().st_size, local_path.stat().st_size, local_path.name
            )
            return entry
        return self._multipart_upload(local_path, remote_dir, rename_on_conflict)

    def _multipart_upload(
        self, local_path: Path, remote_dir: str, rename_on_conflict: bool
    ) -> Entry:
        parent = self.client.resolve(remote_dir, "dir")
        existing = next(
            (
                item
                for item in self.client.list_id(parent.id)
                if item.name == local_path.name
            ),
            None,
        )
        if existing and not rename_on_conflict:
            raise AnyShareError(
                f"Remote item already exists: {remote_dir}/{local_path.name}"
            )
        state_path = _state_path(local_path, parent.id)
        state: dict[str, Any]
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
        else:
            state = self.client._request(
                "POST",
                "/api/efast/v1/file/osinitmultiupload",
                json={
                    "docid": parent.id,
                    "name": local_path.name,
                    "length": local_path.stat().st_size,
                    "ondup": 2 if rename_on_conflict else 1,
                },
            ).json()
            state["parts"] = {}
            _save_state(state_path, state)
        total_parts = max(
            1, (local_path.stat().st_size + self.chunk_size - 1) // self.chunk_size
        )
        signed = self.client._request(
            "POST",
            "/api/efast/v1/file/osuploadpart",
            json={
                "docid": state["docid"],
                "rev": state["rev"],
                "uploadid": state["uploadid"],
                "parts": f"1-{total_parts}",
            },
        ).json()["authrequests"]
        completed = sum(int(value[1]) for value in state["parts"].values())
        if completed:
            self._progress(completed, local_path.stat().st_size, local_path.name)
        with local_path.open("rb") as stream:
            for part_number in range(1, total_parts + 1):
                length = min(
                    self.chunk_size,
                    local_path.stat().st_size - (part_number - 1) * self.chunk_size,
                )
                if str(part_number) in state["parts"]:
                    stream.seek(length, os.SEEK_CUR)
                    continue
                chunk = stream.read(length)
                auth_request = signed.get(str(part_number)) or signed.get(part_number)
                if auth_request is None:
                    auth_request = list(signed.values())[part_number - 1]
                method, url, *header_lines = auth_request
                response = requests.request(
                    method,
                    url,
                    headers=_headers(header_lines),
                    data=chunk,
                    timeout=None,
                )
                if not response.ok:
                    raise AnyShareError(
                        f"Object storage part {part_number} returned HTTP {response.status_code}"
                    )
                etag = response.headers.get("ETag") or response.headers.get("etag")
                if not etag:
                    raise AnyShareError(
                        f"Object storage part {part_number} returned no ETag"
                    )
                state["parts"][str(part_number)] = [etag, len(chunk)]
                completed += len(chunk)
                _save_state(state_path, state)
                self._progress(completed, local_path.stat().st_size, local_path.name)
        response = self.client._request(
            "POST",
            "/api/efast/v1/file/oscompleteupload",
            json={
                "partinfo": state["parts"],
                "docid": state["docid"],
                "rev": state["rev"],
                "uploadid": state["uploadid"],
            },
        )
        try:
            completion = response.json()
        except ValueError:
            completion = response.text
        if not isinstance(completion, str):
            raise AnyShareError("Unexpected multipart completion response")
        self._confirm_multipart(completion)
        completed_data = self.client._request(
            "POST",
            "/api/efast/v1/file/osendupload",
            json={"docid": state["docid"], "rev": state["rev"]},
        ).json()
        state_path.unlink(missing_ok=True)
        return Entry(
            name=completed_data.get("name", local_path.name),
            id=completed_data.get("docid", state["docid"]),
            kind="file",
            size=local_path.stat().st_size,
            rev=completed_data.get("rev", state["rev"]),
        )

    @staticmethod
    def _confirm_multipart(value: str) -> None:
        first_line = value.lstrip().splitlines()[0].strip()
        if not first_line.startswith("--"):
            raise AnyShareError("Multipart completion response has no boundary")
        boundary = first_line[2:]
        sections = [item.strip() for item in value.split(f"--{boundary}")[1:-1]]
        if len(sections) < 2:
            raise AnyShareError("Multipart completion response is incomplete")
        body = sections[0]
        metadata_text = sections[1]
        metadata_text = re.sub(r"^Content-Type:[^\r\n]*\r?\n\r?\n", "", metadata_text)
        metadata = json.loads(metadata_text)
        method, url, *header_lines = metadata["authrequest"]
        response = requests.request(
            method, url, headers=_headers(header_lines), data=body, timeout=None
        )
        if not response.ok:
            raise AnyShareError(
                f"Object storage multipart confirmation returned HTTP {response.status_code}"
            )

    def download(self, remote_path: str, destination: Path) -> list[dict[str, Any]]:
        entry = self.client.resolve(remote_path)
        if entry.kind == "file":
            path = self.client.download(remote_path, destination)
            return [{"remote": remote_path, "path": str(path)}]
        destination = destination.expanduser().resolve()
        root = destination / entry.name if destination.is_dir() else destination
        if root.exists():
            raise AnyShareError(f"Local destination already exists: {root}")
        root.mkdir(parents=True)
        downloaded: list[dict[str, Any]] = []
        try:
            for relative, item in self.client.iter_tree(remote_path):
                local = root / relative
                if item.kind == "dir":
                    local.mkdir()
                    continue
                remote = str(PurePosixPath("/" + remote_path.lstrip("/")) / relative)
                path = self.client.download(remote, local)
                downloaded.append({"remote": remote, "path": str(path)})
                self._progress(len(downloaded), len(downloaded), item.name)
        except Exception:
            print(f"Partial download retained at: {root}", file=sys.stderr)
            raise
        return downloaded
