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

# 网络超时 (秒): 弱网下建立连接可能很慢, 逐项放宽.
# requests 的 timeout 可写成 (连接, 读取) 元组, 二者分别计时:
#   连接 = TCP + TLS 握手完成的最长等待
#   读取 = 服务器首个响应字节 / 传输中两次收包之间的最长等待 (不是整个请求的总时长)
# 注意: DNS 解析不受这两个值约束 (getaddrinfo 在 requests 之外), 域名解析慢时
# 仍可能提前抛 ConnectionError, 那种情况会直接提示网络不可用.
CONNECT_TIMEOUT = 120      # 建立连接 (弱网握手经常超过 60 秒)
LOGIN_TIMEOUT = 180        # oauth/token 等服务器签发 token
API_TIMEOUT = 180          # assignUploadUrl / develop/create
UPLOAD_TIMEOUT = 600       # 预签名 URL 的 PUT 传输间隔

# 自动重试: 弱网抖动大多是"这一次失败、下一次就通", 纯拉长超时救不回来
# (比如 DNS 解析失败、TLS 握手被重置, 都是几秒内直接报错, 与 timeout 无关).
# create 不重试 —— 重复创建会在目标账号里留下同名应用, 语义比重试重要.
LOGIN_ATTEMPTS = 3         # 登录总尝试次数 (含首次)
API_ATTEMPTS = 3           # 获取上传地址 / 创建 的总尝试次数
UPLOAD_ATTEMPTS = 2        # 大包上传只重试一次, 避免长时间重复传输
RETRY_DELAYS = (2.0, 5.0)  # 第 1、2 次失败后的等待秒数

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
