"""内存中构建迁移包: 复制 package.json 并改 uuid/name, 打包 zip. 不触碰磁盘源文件.

打包前会做一次「自定义指令集内联」: 影刀社区版/基础账号不允许使用第三方(自定义)指令集,
直接迁移会被服务端以「社区版或基础账号不能使用高级指令集」拒绝; 因此在这里把用到的
自定义指令集等价改写成应用自己的流程 (详见 custom_activity_inliner)。
"""
import copy
import io
import json
import zipfile
from pathlib import Path
from typing import Callable

from migration_assistant.models import AppInfo, PackagePayload
from migration_assistant.services.custom_activity_inliner import (
    CustomActivityInliner, InlineReport)


class PackageBuilder:
    def __init__(self, inliner: CustomActivityInliner | None = None):
        self.inliner = inliner

    def build(self, app: AppInfo, new_uuid: str, new_name: str,
              on_inline: Callable[[InlineReport], None] | None = None) -> PackagePayload:
        files = self._collect(app.app_dir)

        # 深拷贝: 内联器会改写 flows/依赖声明, 不能污染扫描结果里的原 package
        new_pkg = copy.deepcopy(app.package)
        new_pkg["uuid"] = new_uuid
        new_pkg["name"] = new_name
        files.pop("package.json", None)
        files.pop("package.sigstore", None)   # 迁移成新应用, 不能沿用原签名

        inliner = self.inliner if self.inliner is not None else CustomActivityInliner()
        report = inliner.transform(Path(app.app_dir), files, new_pkg)
        if on_inline is not None:
            on_inline(report)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("package.json", json.dumps(new_pkg, ensure_ascii=False))
            for rel in sorted(files):
                zf.writestr(rel, files[rel])

        json_bytes = json.dumps(new_pkg, ensure_ascii=False).encode("utf-8")
        return PackagePayload(bot_bytes=buf.getvalue(), json_bytes=json_bytes,
                              package=new_pkg)

    @staticmethod
    def _collect(app_dir: Path) -> dict[str, bytes]:
        """读入应用目录 (排除 venv/__pycache__), 键为相对 posix 路径的文件表."""
        files: dict[str, bytes] = {}
        for path in sorted(Path(app_dir).rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(app_dir)
            if "venv" in rel.parts or "__pycache__" in rel.parts:
                continue
            try:
                files[rel.as_posix()] = path.read_bytes()
            except OSError:
                continue
        return files
