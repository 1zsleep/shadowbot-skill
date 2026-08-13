"""配置常量: API 端点、本地路径、应用数据目录 (值来自对 Xbot Deployer.exe 的逆向分析)."""
from pathlib import Path

# ---- 影刀 API (逆向还原) ----
OAUTH_URL = "https://api.yingdao.com/oauth/token"
API_BASE = "https://api.winrobot360.com/api/client"
ASSIGN_UPLOAD_URL = f"{API_BASE}/app/file/assignUploadUrl"
CREATE_URL = f"{API_BASE}/app/develop/create"

CLIENT_ID = "sns:T7svFcIL4foPj1j9"
REQUEST_ID = "57214437-d52d-4f1f-a23f-87c3e9b84adb"
USER_AGENT = "Mozilla/4.0 (compatible; MSIE 9.0; Windows NT 6.1)"

RSA_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCte0XfPY9GUpQ3ZasH1kVb
DhRwyRAqWSeyxj290OqFHtyiZ+5SQjrEr79mk0hcZqV03fb5oYf385E3gopS
ERIKxVQyGoloNeDgyLu7rHHWMPo8KPDpUBlpRpHlGMgBNzJZ2BI6p7LvGAhC
oA7XRuetyTlAW6EbSXBpSu1sNGBhkQIDAQAB
-----END PUBLIC KEY-----"""

# 网络超时 (秒)
LOGIN_TIMEOUT = 30
API_TIMEOUT = 60
UPLOAD_TIMEOUT = 300

# ---- 本地路径 ----
SHADOWBOT_USERS = Path.home() / "AppData" / "Local" / "ShadowBot" / "users"

# ---- 运行数据目录 ----
APP_NAME = "影刀迁移助手"
DATA_DIR = Path.home() / "AppData" / "Local" / "YingdaoMigrationAssistant"
DB_PATH = DATA_DIR / "assistant.db"
LOG_DIR = DATA_DIR / "logs"

# DPAPI 熵 (应用专用, 可重复)
DPAPI_ENTROPY = b"YingdaoMigrationAssistant:v1"

# 迁移后名称模板: 原应用名_云迁_接收于YYYY年MM月DD日HH时mm分ss秒
MIGRATED_NAME_TEMPLATE = "{original}_云迁_接收于{timestamp}"
TIMESTAMP_FORMAT = "%Y年%m月%d日%H时%M分%S秒"
