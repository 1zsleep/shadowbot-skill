"""迁移客户端请求语义测试 (无真实网络)."""
from migration_assistant.errors import MigrationError
from migration_assistant.models import PackagePayload, TaskStage
from migration_assistant.services.migration_client import MigrationClient
from tests.fake_http import FakeResponse, FakeSession


def _payload():
    return PackagePayload(bot_bytes=b"BOT-DATA", json_bytes=b'{"name":"x"}')


def test_login_sets_token_without_retaining_password():
    session = FakeSession()
    session.post_responses.append(FakeResponse({"access_token": "token-1"}))
    client = MigrationClient(session)
    client.login("user@example.com", "secret")
    assert client.access_token == "token-1"
    assert not hasattr(client, "password")
    call = session.calls[0]
    assert call["data"]["password"] != "secret"
    assert call["data"]["grant_type"] == "password"
    assert call["data"]["scope"] == "all"
    assert call["data"]["crypt"] == "metal"


def test_upload_contract_is_bot_strings_and_package_md5():
    session = FakeSession()
    for is_bot in ("true", "false"):
        session.post_responses.append(FakeResponse({
            "success": True, "data": {
                "fileKey": f"robots/x/{is_bot}",
                "uploadUrl": f"https://oss/{is_bot}",
                "fileKeyMd5": f"md5-{is_bot}", "headers": {}}}))
    session.post_responses.append(FakeResponse({"success": True}))
    session.put_responses.append(FakeResponse({}))
    session.put_responses.append(FakeResponse({}))

    client = MigrationClient(session)
    client.access_token = "tok"
    stages = []
    payload = _payload()
    client.upload("new-uuid-123", payload, "日报_云迁_接收于T", {"uia_type": "PC"},
                  lambda s: stages.append(s))

    assigns = [c for c in session.calls if c["method"] == "POST"
               and "assignUploadUrl" in c["url"]]
    creates = [c for c in session.calls if c["method"] == "POST"
               and "develop/create" in c["url"]]
    puts = [c for c in session.calls if c["method"] == "PUT"]

    assert assigns[0]["json"]["isBot"] == "true"
    assert assigns[1]["json"]["isBot"] == "false"
    assert assigns[0]["json"]["appType"] == "app"
    assert assigns[0]["json"]["version"] == ""
    # 抓包还原: assign/create 的 appId 都用新 uuid (与 package.json 一致)
    assert assigns[0]["json"]["appId"] == "new-uuid-123"
    assert creates[0]["json"]["appId"] == "new-uuid-123"
    assert creates[0]["json"]["packageMd5"] == "md5-false"
    assert creates[0]["json"]["appPackage"]["appIcon"] == ""
    assert creates[0]["json"]["elementLibraryStatus"] == 0
    assert puts[0]["data"] == payload.bot_bytes
    assert puts[1]["data"] == payload.json_bytes
    assert stages == [TaskStage.ASSIGNING_BOT_URL, TaskStage.UPLOADING_BOT,
                      TaskStage.ASSIGNING_JSON_URL, TaskStage.UPLOADING_JSON,
                      TaskStage.CREATING, TaskStage.COMPLETE]


def test_create_timeout_is_result_unknown():
    class TimeoutSession(FakeSession):
        def post(self, url, **kwargs):
            self.calls.append({"method": "POST", "url": url, **kwargs})
            if "develop/create" in url:
                import requests
                raise requests.Timeout()
            return self.post_responses.pop(0)

    session = TimeoutSession()
    for is_bot in ("true", "false"):
        session.post_responses.append(FakeResponse({
            "success": True, "data": {
                "fileKey": "k", "uploadUrl": f"https://oss/{is_bot}",
                "fileKeyMd5": f"md5-{is_bot}", "headers": {}}}))
    session.put_responses.append(FakeResponse({}))
    session.put_responses.append(FakeResponse({}))

    client = MigrationClient(session)
    client.access_token = "tok"
    try:
        client.upload("new-uuid-456", _payload(), "n", {"uia_type": "PC"}, lambda s: None)
        raise AssertionError("应抛出 MigrationError")
    except MigrationError as e:
        assert e.code == "create_result_unknown"
        assert e.result_unknown is True
    creates = [c for c in session.calls if "develop/create" in c["url"]]
    assert len(creates) == 1  # 不自动重试


def test_upload_aborts_between_stages_when_stop_requested():
    """点停止后不必等整条链路跑完: 阶段之间会检查并直接取消."""
    session = FakeSession()
    session.post_responses.append(FakeResponse({
        "success": True, "data": {"fileKey": "robots/x/true", "uploadUrl": "https://oss/bot",
                                  "fileKeyMd5": "md5-bot", "headers": {}}}))
    client = MigrationClient(session)
    client.access_token = "tok"

    gates = {"n": 0}

    def should_abort():
        gates["n"] += 1
        return gates["n"] > 1          # 第一个阶段放行, 之后请求停止

    try:
        client.upload("u", _payload(), "n", {"uia_type": "PC"}, lambda s: None,
                      should_abort=should_abort)
    except MigrationError as exc:
        assert exc.code == "cancelled"
    else:
        raise AssertionError("应当取消")
    # 取消了就不该再上传机器人包 / 创建应用
    assert not [c for c in session.calls if c["method"] == "PUT"]
    assert "develop/create" not in " ".join(c["url"] for c in session.calls)


def test_login_unexpected_exception_is_wrapped():
    """未预期异常必须转成 MigrationError, 否则会打死工作线程让界面永久卡住."""

    class BoomSession(FakeSession):
        def post(self, url, **kwargs):
            raise RuntimeError("crypto blew up")

    client = MigrationClient(BoomSession())
    try:
        client.login("u", "p")
    except MigrationError as exc:
        assert exc.code == "login_error"
        assert "RuntimeError" in exc.user_message
    else:
        raise AssertionError("应当包成 MigrationError")
