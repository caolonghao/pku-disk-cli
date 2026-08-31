import pytest

from pku_disk import cli
from pku_disk.client import AnyShareError


class NoWriteClient:
    def remove(self, _path: str) -> None:
        raise AssertionError("remove must not be called without confirmation")

    def restore_trash(self, _item_id: str, _rename: bool) -> None:
        raise AssertionError("restore must not be called without confirmation")

    def delete_trash(self, _item_id: str) -> None:
        raise AssertionError("delete must not be called without confirmation")


@pytest.mark.parametrize(
    "arguments, message",
    [
        (["rm", "/important"], "without --yes"),
        (["trash", "restore", "gns://deleted"], "without --yes"),
        (["trash", "delete", "gns://deleted"], "without --yes"),
    ],
)
def test_destructive_commands_require_confirmation(
    arguments: list[str], message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_client", lambda: NoWriteClient())
    args = cli.build_parser().parse_args(arguments)
    with pytest.raises(AnyShareError, match=message):
        cli.run(args)


def test_forget_session_requires_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli,
        "forget_browser_session",
        lambda: (_ for _ in ()).throw(AssertionError("must not remove session")),
    )
    args = cli.build_parser().parse_args(["auth", "forget-session"])
    with pytest.raises(AnyShareError, match="without --yes"):
        cli.run(args)
