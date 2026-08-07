# 影刀迁移 API 抓包样本 (2026-08-05, 从 Xbot Deployer.exe 实际流量还原)

来源: mitmproxy 抓取 Xbot Deployer.exe 的真实请求 (WinHTTP 代理 127.0.0.1:8080)。
所有请求 User-Agent: `Mozilla/4.0 (compatible; MSIE 9.0; Windows NT 6.1)`

## 1. 登录

```
POST https://api.yingdao.com/oauth/token
Authorization: basic c25zOlQ3c3ZGY0lMNGZvUGoxajk=
Content-Type: application/x-www-form-urlencoded; Charset=UTF-8

body: username=xxx&password=<RSA密文>&crypt=metal&grant_type=password&scope=all

resp 200: {"access_token":"dc4134c8-...","apiType":"oauth2","code":"200",
  "expires_in":2592000,"isFirstClientLogin":false,"scope":"all",
  "success":true,"token_type":"bearer"}
```

## 2. 云端应用列表 (POST! 不是 GET)

```
POST https://api.winrobot360.com/api/client/app/develop/list
body: {"groupId":null,"name":"","pageType":1,"pageDTO":{"page":1,"size":30},"sortBy":"4"}

resp: {"data":[{"appId":"9cc68d19-...","versionId":"ea52d800-...",
  "appName":"整理订单",
  "updateTime":"2026-08-05 15:55:39","icon":"","versionStatus":"d",
  "authority":"owner","internalDependencyPackage":"activity_excel_v2==26.6..."}],
  "code":200,"success":true}
```

注意: 参数错时返回 `{"code":200,"success":true}` 无 data, 不报错。

## 3. assignUploadUrl — isBot=true → package.bot

```
POST https://api.winrobot360.com/api/client/app/file/assignUploadUrl
body: {"appId":"b1ff4a02-a8bd-4560-a3a4-096893cfbd8a","appType":"app","version":"","isBot":"true"}

resp: {"data":{"fileKey":"robots/robot-b1ff4a02-a8bd-4560-a3a4-096893cfbd8a/v-1/package.bot",
  "uploadUrl":"https://winrobot-pri-a.oss-cn-hangzhou.aliyuncs.com/robots/robot-b1ff4a02-.../v-1/package.bot?Expires=1785918335&OSSAccessKeyId=LTAI5t93vfGfysekhtsfrZU9&Signature=TvHzq1z5%2F...",
  "readUrl":"...","fileKeyMd5":"42bf4d5e...","headers":{}},"code":200,"success":true}
```

## 4. assignUploadUrl — isBot=false → package.json

```
body: {"appId":"b1ff4a02-...","appType":"app","version":"","isBot":"false"}
resp fileKeyMd5: 16fc14ae11d91cafa2f4a49ed50301a3   ← 这个值用于 create.packageMd5
```

## 5. PUT 上传 (OSS)

```
PUT <uploadUrl>   ← 无 Content-Type 头! 带了会 403 SignatureDoesNotMatch
- package.bot body = zip (PK 头; 292 文件含 .dev/; 排除 venv/__pycache__; 包内 package.json 已改新 uuid/新名)
- package.json body = 修改后 package.json (含新 uuid/新名, UTF-8)
```

## 6. create (提交)

```
POST https://api.winrobot360.com/api/client/app/develop/create
body: {"appId":"b1ff4a02-...",
  "appPackage":{
    "activities":[],"appFlowParamList":[],"appIcon":"","appType":"app",
    "customItems":{"gifUrl":"","imageName":"","imageUrl":"","uiaType":"PC","videoUrl":""},
    "description":"","elementLibraryCodes":[],"enableViewSource":"false",
    "externalDependencies":["openpyxl==3.0.7","pandas==1.3.5","pywin32==225"],
    "instruction":"","internalDependencies":["activity_excel_v2==26.6.3","shadowbot_text_new==26.1.0","shadowbot_list==25.12.2","shadowbot_datatime==26.1.2"],
    "internalautodependencies":["shadowbot_datatime","activity_excel_v2","shadowbot_text_new","shadowbot_list"],
    "ipaasDependencies":[],"name":"整理订单",
    "packageCode":"","statistics":{"blockCount":1,"flowCount":1,"magicBlockCount":0,"sourceLineCount":0},
    "uiTags":"","uiaType":"PC","videoUrl":""},
  "elementLibraryStatus":0,"groupId":"","packageMd5":"16fc14ae11d91cafa2f4a49ed50301a3"}

resp: {"code":200,"success":true}
```

## 7. 客户端日志模式 (ShadowBot\log\*.log)

```
[UploadApp] UploadAsync start. uuid: d8a1f532-... name: 货件监测
upload: https://winrobot-pri-a.oss-cn-hangzhou.aliyuncs.com/robots/robot-d8a1f532-.../v-19/package.bot?Expires=..., status_code: OK
upload: https://winrobot-pri-a.oss-cn-hangzhou.aliyuncs.com/robots/robot-d8a1f532-.../v-19/package.json?Expires=..., status_code: OK
UploadAppToServer success: appId:d8a1f532-... packageMd5: aa5b25f7... packageJsonMd5:7b9a578c...
```

## 关键洞察

- 上传顺序: assignUploadUrl(isBot=true) → PUT bot → assignUploadUrl(isBot=false) → PUT json → create
- 每次迁移生成**新 uuid** (客户端 uuid4), 应用名加 `_云迁_接收于<YYYY年MM月DD日 HH时MM分SS秒>` 后缀
- `packageMd5` = isBot=false 响应的 `fileKeyMd5` (= md5(fileKey) 字符串, 不是文件内容 md5)
- 一次可选择多个应用, 逐个重复上述流程 (抓包中一次迁移了 5 个)
