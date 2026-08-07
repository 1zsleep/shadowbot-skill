# Captured API formats (from live mitmproxy capture of Xbot Deployer.exe, 2026-08-05)

All request bodies below are the EXACT payloads the original tool sends. Header baseline:
`User-Agent: Mozilla/4.0 (compatible; MSIE 9.0; Windows NT 6.1)` +
`Xybot-Client-RequestId: 57214437-d52d-4f1f-a23f-87c3e9b84adb` (any UUID works).

## 1. Login — POST https://api.yingdao.com/oauth/token

Headers:
```
Authorization: basic c25zOlQ3c3ZGY0lMNGZvUGoxajk=     ← lowercase "basic"!
Content-Type: application/x-www-form-urlencoded; Charset=UTF-8
```
Body (form-urlencoded):
```
username=xxx&password=***&crypt=metal&grant_type=password&scope=all
```
RSA: 1024-bit public key (X.509 SPKI PEM, 见 scripts/migrate_app.py 的 RSA_PUBLIC_KEY 常量), PKCS1v15 padding, base64 output — JSEncrypt-compatible.
Response: `{"access_token":"...","expires_in":2592000,"token_type":"bearer",...}`

## 2. List apps — POST https://api.winrobot360.com/api/client/app/develop/list

Body (JSON):
```json
{"groupId": null, "name": "", "pageType": 1, "pageDTO": {"page": 1, "size": 30}, "sortBy": "4"}
```
Response `data[]`: appId, versionId, appName, updateTime, icon, versionStatus ("d"), authority ("owner"),
internalDependencyPackage, etc.

## 3. Get upload URL — POST https://api.winrobot360.com/api/client/app/file/assignUploadUrl

Call TWICE per app, same body except isBot:

```json
{"appId": "<NEW-UUID>", "appType": "app", "version": "", "isBot": "true"}
{"appId": "<NEW-UUID>", "appType": "app", "version": "", "isBot": "false"}
```
Response `data`: `fileKey` (`robots/robot-<uuid>/v-<ver>/package.bot|package.json`), `uploadUrl` (presigned OSS PUT),
`readUrl`, `fileKeyMd5` (server-computed md5 of the fileKey string), `headers: {}`.

## 4. Upload — PUT <uploadUrl>

- Raw bytes body. **NO Content-Type header** (any Content-Type → 403 SignatureDoesNotMatch).
- package.bot = zip of the whole xbot_robot dir (includes `.dev/`, excludes venv/__pycache__).
- package.json = rewritten package.json (new uuid + 云迁 name), UTF-8.

## 5. Create — POST https://api.winrobot360.com/api/client/app/develop/create

```json
{
  "appId": "<NEW-UUID>",
  "appPackage": {
    "activities": [],
    "appFlowParamList": [],
    "appIcon": "",
    "appType": "app",
    "customItems": {"gifUrl": "", "imageName": "", "imageUrl": "", "uiaType": "PC", "videoUrl": ""},
    "description": "",
    "elementLibraryCodes": [],
    "enableViewSource": "false",
    "externalDependencies": ["openpyxl==3.0.7", "pandas==1.3.5", "pywin32==225"],
    "instruction": "",
    "internalDependencies": ["shadowbot_text_new==26.1.0", "shadowbot_list==25.12.2", "activity_excel_v2==26.5.3", "shadowbot_datatime==26.1.2"],
    "internalautodependencies": ["shadowbot_datatime", "activity_excel_v2", "shadowbot_text_new", "shadowbot_list"],
    "ipaasDependencies": [],
    "name": "整理订单",
    "packageCode": "",
    "statistics": {"blockCount": 1, "flowCount": 1, "magicBlockCount": 0, "sourceLineCount": 0},
    "uiTags": "",
    "uiaType": "PC",
    "videoUrl": ""
  },
  "elementLibraryStatus": 0,
  "groupId": "",
  "packageMd5": "<fileKeyMd5 from the isBot=false assignUploadUrl response>"
}
```
Response success: `{"code":200,"success":true}`.

Key relationships verified from capture:
- `packageMd5` == fileKeyMd5 returned by assignUploadUrl(isBot=false) — NOT md5 of uploaded bytes.
- md5 of the `fileKey` STRING == the fileKeyMd5 the server returns.
- PUT package.bot and PUT package.json use uploadUrls from isBot=true / isBot=false calls respectively.
- New uuid per migration; name always gets `_云迁_接收于<ts>` suffix (same ts pattern in both zip json and create body).

## Local package.json fields used by create

`name`, `uuid`, `version`, `external_dependencies`, `internaldependencies`, `internalautodependencies`,
`ipaasDependencies`, `uia_type` (→ appPackage.uiaType + customItems.uiaType), `description`, `instruction`.
