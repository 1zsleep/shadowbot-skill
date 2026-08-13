"""领域异常."""


class MigrationError(RuntimeError):
    def __init__(self, code: str, user_message: str, *, result_unknown: bool = False):
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.result_unknown = result_unknown


class ScanRootMissing(MigrationError):
    def __init__(self, path):
        super().__init__("scan_root_missing", f"未找到影刀用户目录: {path}")
        self.path = path


class CredentialUnavailable(MigrationError):
    def __init__(self):
        super().__init__("credential_unavailable", "本地凭据不可用，请重新输入密码")
