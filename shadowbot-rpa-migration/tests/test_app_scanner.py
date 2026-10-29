"""应用扫描与内存打包测试."""
import json
import re
import zipfile
from io import BytesIO

import pytest

from migration_assistant.errors import ScanRootMissing
from migration_assistant.services.app_scanner import AppScanner
from migration_assistant.services.package_builder import PackageBuilder


def test_scan_returns_valid_apps_and_reports_invalid(apps_tree):
    result = AppScanner().scan(apps_tree)
    assert [app.name for app in result.apps] == ["日报生成", "销售同步"]
    assert result.apps[0].source_user == "u1"
    assert result.skipped_count == 1


def test_scan_missing_root_raises(tmp_path):
    missing = tmp_path / "missing"
    with pytest.raises(ScanRootMissing, match=re.escape(str(missing))):
        AppScanner().scan(missing)


def test_scan_fills_account_names_from_xml_and_db(tmp_path):
    """扫描时应从 Account.xml 和 user.db3 填充账号名/显示名."""
    users = tmp_path / "users"
    # 模拟 Account.xml: 登录名 <-> 显示名
    (users).mkdir(parents=True)
    (users / "Account.xml").write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<ArrayOfAccountInfo>
  <AccountInfo>
    <Name>demo-account-1</Name>
    <UserName>demo-user-1</UserName>
    <UserInfoDisplayName>演示账号一</UserInfoDisplayName>
  </AccountInfo>
  <AccountInfo>
    <Name>demo-user-2@example.com</Name>
    <UserName>demo-user-2@example.com</UserName>
    <UserInfoDisplayName>演示账号二</UserInfoDisplayName>
  </AccountInfo>
</ArrayOfAccountInfo>""",
        encoding="utf-8")
    # 用户目录 + 应用 + user.db3 (uuid -> ownerName)
    import sqlite3
    uid = "100000000000000001"
    app_uuid = "app-uuid-1"
    app_dir = users / uid / "apps" / app_uuid / "xbot_robot"
    app_dir.mkdir(parents=True)
    (app_dir / "package.json").write_text(
        json.dumps({"name": "日报生成", "uuid": app_uuid}), encoding="utf-8")
    conn = sqlite3.connect(str(users / uid / "user.db3"))
    conn.execute("CREATE TABLE developmentsync_apps_v2 (uuid TEXT, ownerName TEXT)")
    conn.execute("INSERT INTO developmentsync_apps_v2 VALUES (?, ?)",
                 (app_uuid, "demo-user-2@example.com"))
    conn.commit()
    conn.close()

    result = AppScanner().scan(users)
    assert len(result.apps) == 1
    app = result.apps[0]
    assert app.owner_name == "demo-user-2@example.com"
    assert app.display_name == "演示账号二"  # Account.xml 映射
    assert app.source_user == uid


def test_scan_falls_back_to_owner_when_no_display_name(tmp_path):
    """Account.xml 里没有的账号, 显示名回退为账号名."""
    users = tmp_path / "users"
    users.mkdir()
    (users / "Account.xml").write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<ArrayOfAccountInfo></ArrayOfAccountInfo>""", encoding="utf-8")
    import sqlite3
    uid = "u1"
    app_uuid = "a1"
    app_dir = users / uid / "apps" / app_uuid / "xbot_robot"
    app_dir.mkdir(parents=True)
    (app_dir / "package.json").write_text(
        json.dumps({"name": "销售同步", "uuid": app_uuid}), encoding="utf-8")
    conn = sqlite3.connect(str(users / uid / "user.db3"))
    conn.execute("CREATE TABLE developmentsync_apps_v2 (uuid TEXT, ownerName TEXT)")
    conn.execute("INSERT INTO developmentsync_apps_v2 VALUES (?, ?)",
                 (app_uuid, "demo-user-3@example.com"))
    conn.commit()
    conn.close()

    result = AppScanner().scan(users)
    app = result.apps[0]
    assert app.owner_name == "demo-user-3@example.com"
    assert app.display_name == "demo-user-3@example.com"  # 回退


def test_build_changes_only_in_memory(sample_app):
    original = sample_app.package_path.read_bytes()
    payload = PackageBuilder().build(
        sample_app, "new-id", "日报生成_云迁_接收于2026年08月05日17时30分25秒")

    assert sample_app.package_path.read_bytes() == original
    with zipfile.ZipFile(BytesIO(payload.bot_bytes)) as archive:
        package = json.loads(archive.read("package.json"))
        assert package["uuid"] == "new-id"
        assert "__pycache__/main.pyc" not in archive.namelist()
    assert json.loads(payload.json_bytes)["name"].startswith("日报生成_云迁_")
    assert json.loads(payload.json_bytes)["uuid"] == "new-id"
