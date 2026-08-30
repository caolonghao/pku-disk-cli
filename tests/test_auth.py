from pku_disk.auth import _anyshare_token, _successful_anyshare_token


class Request:
    def __init__(self, url: str, authorization: str) -> None:
        self.url = url
        self.headers = {"authorization": authorization}


class Response:
    def __init__(self, status: int, request: Request) -> None:
        self.status = status
        self.request = request


def test_accepts_only_anyshare_api_bearer_token() -> None:
    request = Request(
        "https://disk.pku.edu.cn/api/efast/v1/folders/gns%3A%2F%2FROOT/sub_objects",
        "Bearer anyshare-token",
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


def test_accepts_token_only_after_successful_api_response() -> None:
    request = Request(
        "https://disk.pku.edu.cn/api/efast/v1/folders/gns%3A%2F%2FROOT/sub_objects",
        "Bearer fresh-token",
    )
    assert _successful_anyshare_token(Response(200, request)) == "fresh-token"
    assert _successful_anyshare_token(Response(401, request)) is None


def test_rejects_public_anyshare_api_even_when_successful() -> None:
    request = Request(
        "https://disk.pku.edu.cn/api/metadata/v1/ping", "Bearer stale-token"
    )
    assert _successful_anyshare_token(Response(200, request)) is None
