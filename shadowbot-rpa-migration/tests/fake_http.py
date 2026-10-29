"""测试工具: fake HTTP 会话."""
import requests


class FakeResponse:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeSession:
    """记录调用并按顺序返回预设响应."""
    def __init__(self):
        self.headers: dict = {}
        self.post_responses: list[FakeResponse] = []
        self.put_responses: list[FakeResponse] = []
        self.calls: list[dict] = []

    def post(self, url, **kwargs):
        self.calls.append({"method": "POST", "url": url, **kwargs})
        return self.post_responses.pop(0) if self.post_responses else FakeResponse({})

    def put(self, url, **kwargs):
        self.calls.append({"method": "PUT", "url": url, **kwargs})
        return self.put_responses.pop(0) if self.put_responses else FakeResponse({})
