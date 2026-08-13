"""只读扫描本地影刀应用 (不修改任何文件).

来源账号信息:
  - users/Account.xml: 登录名(Name) <-> 显示名(UserInfoDisplayName) 映射
  - users/<id>/user.db3 developmentsync_apps_v2: 应用uuid -> ownerName
"""
import json
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

from migration_assistant.errors import ScanRootMissing
from migration_assistant.models import AppInfo, ScanResult


class AppScanner:
    def scan(self, users_dir: Path) -> ScanResult:
        users_dir = Path(users_dir)
        if not users_dir.is_dir():
            raise ScanRootMissing(users_dir)

        # 登录名 <-> 显示名 映射 (Account.xml)
        name_to_display, display_to_name = self._load_account_aliases(users_dir)

        apps: list[AppInfo] = []
        skipped = 0

        for user_dir in sorted(users_dir.iterdir()):
            if not user_dir.is_dir():
                continue
            apps_dir = user_dir / "apps"
            if not apps_dir.is_dir():
                continue
            # 该用户目录的应用 uuid -> ownerName (user.db3)
            owner_by_uuid = self._load_owners(user_dir)
            for app_dir in sorted(apps_dir.iterdir()):
                pkg_path = app_dir / "xbot_robot" / "package.json"
                if not pkg_path.exists():
                    continue
                try:
                    pkg = json.loads(pkg_path.read_text(encoding="utf-8-sig"))
                except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                    skipped += 1
                    continue
                name = pkg.get("name")
                if not isinstance(name, str) or not name.strip():
                    skipped += 1
                    continue
                owner_name = owner_by_uuid.get(app_dir.name, "")
                display_name = name_to_display.get(owner_name, owner_name)
                apps.append(AppInfo(
                    name=name.strip(),
                    source_user=user_dir.name,
                    app_dir=pkg_path.parent,
                    package_path=pkg_path,
                    package=pkg,
                    owner_name=owner_name or display_name,
                    display_name=display_name,
                ))

        return ScanResult(apps=tuple(apps), skipped_count=skipped)

    @staticmethod
    def _load_account_aliases(users_dir: Path):
        """解析 Account.xml -> (登录名->显示名, 显示名->登录名)."""
        name_to_display: dict[str, str] = {}
        display_to_name: dict[str, str] = {}
        account_xml = users_dir / "Account.xml"
        if not account_xml.exists():
            return name_to_display, display_to_name
        try:
            root = ET.parse(account_xml).getroot()
            for info in root.iter("AccountInfo"):
                name_el = info.find("Name")
                display_el = info.find("UserInfoDisplayName")
                if name_el is None or display_el is None:
                    continue
                name = (name_el.text or "").strip()
                display = (display_el.text or "").strip()
                if name and display:
                    name_to_display[name] = display
                    display_to_name[display] = name
        except (ET.ParseError, OSError):
            pass
        return name_to_display, display_to_name

    @staticmethod
    def _load_owners(user_dir: Path) -> dict[str, str]:
        """读取 user.db3 -> {应用uuid: ownerName}."""
        db_path = user_dir / "user.db3"
        if not db_path.exists():
            return {}
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                rows = conn.execute(
                    "SELECT uuid, ownerName FROM developmentsync_apps_v2"
                ).fetchall()
            finally:
                conn.close()
        except (sqlite3.Error, OSError):
            return {}
        return {str(r[0]): str(r[1] or "") for r in rows}
