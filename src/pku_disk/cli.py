from __future__ import annotations

import argparse
import getpass
import importlib.resources
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .auth import BrowserLoginError, browser_login
from .client import AnyShareClient, AnyShareError, Entry
from .credentials import CredentialError, delete_token, load_token, save_token
from .sharing import ACCESSOR_TYPES, PERMISSION_CHOICES, SharingClient


def _size(value: int) -> str:
    if value < 0:
        return "-"
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return str(value)


def _emit(value: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        print(value)


def _client() -> AnyShareClient:
    return AnyShareClient(load_token())


def _entry_output(entry: Entry) -> dict[str, Any]:
    return entry.as_dict()


def _add_permissions(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--permission",
        action="append",
        choices=PERMISSION_CHOICES,
        dest="permissions",
        help="Repeat to combine permissions",
    )


def _password(args: argparse.Namespace) -> str | None:
    if getattr(args, "clear_password", False):
        return ""
    if not getattr(args, "password", False):
        return None
    value = os.environ.get("PKU_DISK_SHARE_PASSWORD")
    if value is None:
        value = getpass.getpass("Share password: ")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pku-disk", description="PKU AnyShare command line client"
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    auth = commands.add_parser("auth", help="Manage authentication")
    auth_commands = auth.add_subparsers(dest="auth_command", required=True)
    login = auth_commands.add_parser(
        "login", help="Log in through PKU SSO in a temporary browser"
    )
    login.add_argument(
        "--timeout", type=int, default=300, help="Login timeout in seconds"
    )
    auth_commands.add_parser(
        "import-token", help="Securely import an existing Bearer token"
    )
    auth_commands.add_parser("status", help="Test the saved token")
    auth_commands.add_parser("logout", help="Remove the saved token")

    ls = commands.add_parser("ls", help="List a remote directory")
    ls.add_argument("path", nargs="?", default="/")
    ls.add_argument("--json", action="store_true")

    tree = commands.add_parser("tree", help="Recursively list a remote directory")
    tree.add_argument("path", nargs="?", default="/")
    tree.add_argument("--json", action="store_true")

    stat = commands.add_parser("stat", help="Show metadata for a remote item")
    stat.add_argument("path")
    stat.add_argument("--json", action="store_true")

    mkdir = commands.add_parser("mkdir", help="Create a remote directory")
    mkdir.add_argument("path")
    mkdir.add_argument("-p", "--parents", action="store_true")
    mkdir.add_argument("--json", action="store_true")

    put = commands.add_parser("put", help="Upload one local file")
    put.add_argument("local", type=Path)
    put.add_argument("remote_dir", nargs="?", default="/")
    put.add_argument("--rename-on-conflict", action="store_true")
    put.add_argument("--json", action="store_true")

    get = commands.add_parser("get", help="Download one remote file")
    get.add_argument("remote")
    get.add_argument("destination", nargs="?", type=Path, default=Path("."))
    get.add_argument("--json", action="store_true")

    share = commands.add_parser(
        "share", help="Manage anonymous links and internal grants"
    )
    share_commands = share.add_subparsers(dest="share_command", required=True)
    policy = share_commands.add_parser("policy", help="Show server sharing policy")
    policy.add_argument("--json", action="store_true")
    share_list = share_commands.add_parser("list", help="List all managed shares")
    share_list.add_argument(
        "--type", choices=("all", "anonymous", "realname"), default="all"
    )
    share_list.add_argument("--json", action="store_true")

    link = share_commands.add_parser("link", help="Manage anonymous share links")
    link_commands = link.add_subparsers(dest="link_command", required=True)
    link_create = link_commands.add_parser("create", help="Create an anonymous link")
    link_create.add_argument("path")
    _add_permissions(link_create)
    link_create.add_argument("--expires-days", type=int, default=30)
    link_create.add_argument("--title")
    link_create.add_argument(
        "--password", action="store_true", help="Prompt securely for a password"
    )
    link_create.add_argument("--max-uses", type=int, default=-1)
    link_create.add_argument("--json", action="store_true")
    link_list = link_commands.add_parser("list", help="List links for a remote item")
    link_list.add_argument("path")
    link_list.add_argument("--json", action="store_true")
    link_show = link_commands.add_parser("show", help="Show one link")
    link_show.add_argument("link_id")
    link_show.add_argument("--json", action="store_true")
    link_update = link_commands.add_parser("update", help="Update one link")
    link_update.add_argument("link_id")
    _add_permissions(link_update)
    link_update.add_argument("--expires-days", type=int)
    link_update.add_argument("--title")
    password_group = link_update.add_mutually_exclusive_group()
    password_group.add_argument(
        "--password", action="store_true", help="Prompt securely for a password"
    )
    password_group.add_argument("--clear-password", action="store_true")
    link_update.add_argument("--max-uses", type=int)
    link_update.add_argument("--json", action="store_true")
    link_revoke = link_commands.add_parser("revoke", help="Revoke one link")
    link_revoke.add_argument("link_id")
    link_revoke.add_argument("--yes", action="store_true", help="Confirm revocation")
    link_revoke.add_argument("--json", action="store_true")

    accessors = share_commands.add_parser(
        "accessors", help="Search PKU users, departments, and groups"
    )
    accessors.add_argument("query")
    accessors.add_argument("--limit", type=int, default=20)
    accessors.add_argument("--json", action="store_true")
    grants = share_commands.add_parser("grants", help="List internal grants on an item")
    grants.add_argument("path")
    grants.add_argument("--json", action="store_true")
    grant = share_commands.add_parser(
        "grant", help="Grant access to an internal accessor"
    )
    grant.add_argument("path")
    grant.add_argument("--accessor-id", required=True)
    grant.add_argument("--accessor-type", choices=ACCESSOR_TYPES, required=True)
    grant.add_argument("--accessor-name", default="")
    _add_permissions(grant)
    grant.add_argument("--expires-days", type=int, default=0)
    grant.add_argument("--json", action="store_true")
    revoke = share_commands.add_parser(
        "revoke", help="Revoke one direct internal grant"
    )
    revoke.add_argument("path")
    revoke.add_argument("--accessor-id", required=True)
    revoke.add_argument("--accessor-type", choices=ACCESSOR_TYPES, required=True)
    revoke.add_argument("--yes", action="store_true", help="Confirm revocation")
    revoke.add_argument("--json", action="store_true")

    skill = commands.add_parser("skill", help="Install the bundled Codex skill")
    skill_commands = skill.add_subparsers(dest="skill_command", required=True)
    install_skill = skill_commands.add_parser(
        "install", help="Install the skill into Codex"
    )
    install_skill.add_argument(
        "--force", action="store_true", help="Replace an existing installation"
    )
    return parser


def run(args: argparse.Namespace) -> int:
    if args.command == "skill" and args.skill_command == "install":
        codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        destination = codex_home / "skills" / "pku-disk"
        if destination.exists():
            if not args.force:
                raise AnyShareError(
                    f"Skill already exists: {destination}. Use --force to replace it."
                )
            shutil.rmtree(destination)
        source = importlib.resources.files("pku_disk").joinpath("bundled_skill")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with importlib.resources.as_file(source) as source_path:
            shutil.copytree(source_path, destination)
        print(f"Installed PKU Disk skill at {destination}")
        return 0

    if args.command == "auth":
        if args.auth_command == "login":
            print(
                "Complete PKU IAAA authentication in the opened browser...",
                file=sys.stderr,
            )
            browser_login(timeout=args.timeout)
            print("Authentication token saved securely.")
            return 0
        if args.auth_command == "import-token":
            token = getpass.getpass("AnyShare Bearer token: ")
            save_token(token.removeprefix("Bearer ").removeprefix("bearer "))
            print("Authentication token saved securely.")
            return 0
        if args.auth_command == "logout":
            print(
                "Authentication token removed."
                if delete_token()
                else "No saved token found."
            )
            return 0
        if args.auth_command == "status":
            client = _client()
            count = len(client.list_path("/"))
            print(f"Authenticated. Root contains {count} items.")
            return 0

    client = _client()
    if args.command == "share":
        sharing = SharingClient(client)
        if args.share_command == "policy":
            value = sharing.policy()
            _emit(
                value if args.json else json.dumps(value, ensure_ascii=False, indent=2),
                args.json,
            )
            return 0
        if args.share_command == "list":
            value = sharing.list_all(args.type)
            _emit(
                value if args.json else json.dumps(value, ensure_ascii=False, indent=2),
                args.json,
            )
            return 0
        if args.share_command == "accessors":
            value = sharing.search_accessors(args.query, args.limit)
            if args.json:
                _emit(value, True)
            else:
                for item in value:
                    label = item["account"] or item["path"]
                    print(f"{item['type']:<10} {item['id']}  {item['name']}  {label}")
            return 0
        if args.share_command == "grants":
            value = sharing.list_grants(args.path)
            _emit(
                value if args.json else json.dumps(value, ensure_ascii=False, indent=2),
                args.json,
            )
            return 0
        if args.share_command == "grant":
            value = sharing.grant(
                args.path,
                args.accessor_id,
                args.accessor_type,
                args.permissions or ["preview", "download"],
                args.expires_days,
                args.accessor_name,
            )
            _emit(value if args.json else "Grant saved.", args.json)
            return 0
        if args.share_command == "revoke":
            if not args.yes:
                raise AnyShareError("Refusing to revoke without --yes")
            value = sharing.revoke_grant(
                args.path, args.accessor_id, args.accessor_type
            )
            _emit(value if args.json else "Grant revoked.", args.json)
            return 0
        if args.share_command == "link":
            if args.link_command == "create":
                value = sharing.create_link(
                    args.path,
                    args.permissions or ["preview", "download"],
                    args.expires_days,
                    args.title,
                    _password(args) or "",
                    args.max_uses,
                )
            elif args.link_command == "list":
                value = sharing.list_links(args.path)
            elif args.link_command == "show":
                value = sharing.get_link(args.link_id)
            elif args.link_command == "update":
                password = _password(args)
                if all(
                    item is None
                    for item in (
                        args.permissions,
                        args.expires_days,
                        args.title,
                        args.max_uses,
                        password,
                    )
                ):
                    raise AnyShareError("No link changes specified")
                value = sharing.update_link(
                    args.link_id,
                    args.permissions,
                    args.expires_days,
                    args.title,
                    password,
                    args.max_uses,
                )
            else:
                if not args.yes:
                    raise AnyShareError("Refusing to revoke without --yes")
                value = sharing.revoke_link(args.link_id)
            _emit(
                value if args.json else json.dumps(value, ensure_ascii=False, indent=2),
                args.json,
            )
            return 0
    if args.command == "ls":
        entries = client.list_path(args.path)
        if args.json:
            _emit([_entry_output(item) for item in entries], True)
        else:
            for item in entries:
                print(f"{item.kind:<4} {_size(item.size):>10}  {item.name}")
        return 0
    if args.command == "tree":
        entries = list(client.iter_tree(args.path))
        if args.json:
            _emit(
                [{"path": path, **_entry_output(item)} for path, item in entries], True
            )
        else:
            for path, item in entries:
                print(f"{item.kind:<4} {_size(item.size):>10}  {path}")
        return 0
    if args.command == "stat":
        item = client.resolve(args.path)
        _emit(
            _entry_output(item)
            if args.json
            else f"{item.kind} {_size(item.size)} {item.name}\n{item.id}",
            args.json,
        )
        return 0
    if args.command == "mkdir":
        item = client.mkdir(args.path, args.parents)
        _emit(_entry_output(item) if args.json else item.id, args.json)
        return 0
    if args.command == "put":
        item = client.upload(args.local, args.remote_dir, args.rename_on_conflict)
        _emit(_entry_output(item) if args.json else item.id, args.json)
        return 0
    if args.command == "get":
        destination = client.download(args.remote, args.destination)
        _emit({"path": str(destination)} if args.json else str(destination), args.json)
        return 0
    return 2


def main() -> None:
    try:
        raise SystemExit(run(build_parser().parse_args()))
    except (AnyShareError, CredentialError, BrowserLoginError) as exc:
        print(f"pku-disk: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except KeyboardInterrupt:
        print("pku-disk: interrupted", file=sys.stderr)
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
