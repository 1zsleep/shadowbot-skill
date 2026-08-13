"""内存中构建迁移包: 复制 package.json 并改 uuid/name, 打包 zip. 不触碰磁盘源文件."""
import io
import json
import zipfile

from migration_assistant.models import AppInfo, PackagePayload


class PackageBuilder:
    def build(self, app: AppInfo, new_uuid: str, new_name: str) -> PackagePayload:
        new_pkg = dict(app.package)
        new_pkg["uuid"] = new_uuid
        new_pkg["name"] = new_name

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(app.app_dir.rglob("*")):
                if not p.is_file():
                    continue
                rel = p.relative_to(app.app_dir)
                if "venv" in rel.parts or "__pycache__" in rel.parts:
                    continue
                if p.name == "package.json":
                    zf.writestr("package.json", json.dumps(new_pkg, ensure_ascii=False))
                else:
                    zf.write(p, rel)

        json_bytes = json.dumps(new_pkg, ensure_ascii=False).encode("utf-8")
        return PackagePayload(bot_bytes=buf.getvalue(), json_bytes=json_bytes)
