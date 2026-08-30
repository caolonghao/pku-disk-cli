from pku_disk.auth import _anyshare_token


class Request:
    def __init__(self, url: str, authorization: str) -> None:
        self.url = url
        self.headers = {"authorization": authorization}


def test_accepts_only_anyshare_api_bearer_token() -> None:
    request = Request(
        "https://disk.pku.edu.cn/api/efast/v1/entry-doc-lib", "Bearer anyshare-token"
    )
    assert _anyshare_token(request) == "anyshare-token"


def test_rejects_bearer_token_from_identity_provider() -> None:
    request = Request(
        "https://iaaa.pku.edu.cn/iaaa/oauth.jsp", "Bearer identity-provider-token"
    )
    assert _anyshare_token(request) is None


def test_rejects_non_api_request() -> None:
    request = Request(
        "https://disk.pku.edu.cn/anyshare/zh-cn/", "Bearer unrelated-token"
    )
    assert _anyshare_token(request) is None
