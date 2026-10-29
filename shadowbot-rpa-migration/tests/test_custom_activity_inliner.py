"""自定义指令集内联器测试: 用 tmp_path 造最小应用, 验证转换与依赖清理."""
import json

import pytest

from migration_assistant.services.custom_activity_inliner import CustomActivityInliner
from migration_assistant.services.package_builder import PackageBuilder
from migration_assistant.models import AppInfo

CUSTOM = "activity_abc123"
OFFICIAL = "activity_official"


def _write(p, content):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _custom_ext(ext_root, lossless=True, package_version=8):
    base = ext_root / CUSTOM
    _write(base / "package.json", json.dumps({
        "activity_code": CUSTOM, "name": "ERP文件下载", "version": "26.9.3",
        "package_version": package_version, "tags": "", "icon": None,
        "flows": [{"filename": "main", "name": "main"},
                  {"filename": "process1", "name": "下载1"}]}, ensure_ascii=False))
    _write(base / "prototype.block.json", json.dumps({"types": [], "blocks": [
        {"name": f"xbot_extensions.{CUSTOM}.main", "title": "ERP文件下载",
         "function": f"xbot_extensions.{CUSTOM}.main", "extension": "ERP文件下载",
         "inputs": [{"name": "文件夹", "type": "str", "default": "10:"}], "outputs": []},
        {"name": f"xbot_extensions.{CUSTOM}.process1", "title": "下载1",
         "function": f"xbot_extensions.{CUSTOM}.process1", "extension": "ERP文件下载",
         "inputs": [{"name": "web_page", "type": "xbot._web.browser.WebBrowser"}],
         "outputs": []}]}, ensure_ascii=False))
    main_body = ("        _ = xbot_visual.process.run(process=\"process1\", package=__name__, "
                 "inputs={\"web_page\": None}, outputs=[], _block=(\"main\", 1, \"调用流程\"))\n")
    if not lossless:
        main_body = "        import pandas as pd\n" + main_body
    _write(base / "main.py", (
        "import xbot, xbot_visual\nfrom . import package\n\n"
        "def main(args):\n"
        "    if args is None:\n"
        "        文件夹 = \"\"\n"
        "    else:\n"
        "        文件夹 = args.get(\"文件夹\", \"\")\n"
        "    try:\n" + main_body +
        "    finally:\n        pass\n"))
    _write(base / "process1.py", (
        "import xbot, xbot_visual\nfrom . import package\n\n"
        "def main(args):\n"
        "    if args is None:\n"
        "        web_page = None\n"
        "    else:\n"
        "        web_page = args.get(\"web_page\", None)\n"
        "    try:\n"
        "        xbot_visual.web.element.click(browser=web_page, "
        "element=package.selector(\"下载按钮\"), simulate=True, "
        "_block=(\"下载1\", 1, \"点击元素(web)\"))\n"
        "    finally:\n        pass\n"))
    _write(base / "selectorsV2.xml",
           "<repository><group id=\"g1\" name=\"下载中心\" type=\"Web\" "
           "processName=\"chrome\"><selector id=\"s1\" name=\"下载按钮\" type=\"simple\" "
           "framework=\"chrome\"/></group></repository>")


def _official_ext(ext_root):
    base = ext_root / OFFICIAL
    _write(base / "package.json", json.dumps({
        "activity_code": OFFICIAL, "name": "网页扩展操作", "version": "26.8.0",
        "tags": "official",
        "icon": "https://winrobot-pub-a.oss-cn-hangzhou.aliyuncs.com/x.png",
        "flows": [{"filename": "main", "name": "main"}]}, ensure_ascii=False))
    _write(base / "main.py", "import xbot, xbot_visual\nfrom . import package\n"
                            "def main(args):\n    pass\n")


def _robot(robot_dir, ext_root):
    _write(robot_dir / "process1.py", (
        "import xbot, xbot_visual\nfrom . import package\n\n"
        "def main(args):\n    try://n        pass\n    finally://n        pass\n"))
    _write(robot_dir / "package.json", json.dumps({
        "name": "测试", "uuid": "u",
        "flows": [{"filename": "main", "name": "main"},
                  {"filename": "process1", "name": "应用自己的流程1"}],
        "internaldependencies": [f"{CUSTOM}==26.9.3", f"{OFFICIAL}==26.8.0"],
        "internalautodependencies": [CUSTOM, OFFICIAL]}, ensure_ascii=False))
    _write(robot_dir / "main.py", (
        "import xbot, xbot_visual\nfrom . import package\n\n"
        "def main(args):\n"
        "    try:\n"
        "        xbot_visual.process.run(process=\"xbot_extensions."
        f"{CUSTOM}.main\", package=__name__, inputs={{\"文件夹\": \"D:/下载\"}}, "
        "outputs=[], _block=(\"main\", 1, \"ERP文件下载\"))\n"
        "    finally:\n        pass\n"))
    _write(robot_dir / ".dev" / "main.flow.json", json.dumps({
        "name": "main", "memo": "", "kind": "visual",
        "blocks": [{"id": "b1", "name": f"xbot_extensions.{CUSTOM}.main",
                    "isEnabled": True,
                    "inputs": {"文件夹": {"value": "10:D:/下载"}}, "outputs": {},
                    "block_title": "ERP文件下载/ERP文件下载"}],
        "parameters": []}, ensure_ascii=False))
    _write(robot_dir / "selectorsV2.xml", "<repository></repository>")


@pytest.fixture
def app(tmp_path):
    robot = tmp_path / "app" / "xbot_robot"
    ext_root = tmp_path / "app" / "xbot_extensions"
    _robot(robot, ext_root)
    _custom_ext(ext_root)
    _official_ext(ext_root)
    return robot


def test_inliner_converts_and_cleans(app):
    files = PackageBuilder._collect(app)
    package = json.loads((app / "package.json").read_text(encoding="utf-8"))
    report = CustomActivityInliner().transform(app, files, package)

    assert report.skipped == []
    assert any("ERP文件下载" in item for item in report.converted)
    # 依赖只留官方
    assert package["internaldependencies"] == [f"{OFFICIAL}==26.8.0"]
    assert package["internalautodependencies"] == [OFFICIAL]
    # 新流程已注册 (应用自己的 main/process1 不算)
    new_flows = [f for f in package["flows"] if f["filename"] not in ("main", "process1")]
    assert new_flows
    new_file = new_flows[0]["filename"]
    assert f"{new_file}.py" in files
    assert f".dev/{new_file}.flow.json" in files
    # 调用已改写
    assert "xbot_extensions" not in files["main.py"].decode("utf-8")
    flow_json = json.loads(files[".dev/main.flow.json"].decode("utf-8"))
    assert flow_json["blocks"][0]["name"] == "process.run"
    # 元素库已并入
    assert "下载按钮" in files["selectorsV2.xml"].decode("utf-8")


def test_official_extensions_are_untouched(app):
    files = PackageBuilder._collect(app)
    package = json.loads((app / "package.json").read_text(encoding="utf-8"))
    CustomActivityInliner().transform(app, files, package)
    assert f"{OFFICIAL}==26.8.0" in package["internaldependencies"]


def test_non_lossless_is_skipped_and_kept(app, tmp_path):
    _custom_ext(tmp_path / "app" / "xbot_extensions", lossless=False)
    files = PackageBuilder._collect(app)
    package = json.loads((app / "package.json").read_text(encoding="utf-8"))
    report = CustomActivityInliner().transform(app, files, package)
    assert report.skipped
    # 无法无损转换的指令集, 依赖原样保留
    assert f"{CUSTOM}==26.9.3" in package["internaldependencies"]


def test_package_builder_does_not_mutate_source(app):
    from pathlib import Path as P
    info = AppInfo("测试", "u", app, app / "package.json",
                   json.loads((app / "package.json").read_text(encoding="utf-8")))
    original_flows = list(info.package["flows"])
    PackageBuilder().build(info, "new-uuid", "新名")
    # 源 package 未被内联器污染
    assert len(info.package["flows"]) == len(original_flows)


def test_low_package_version_custom_ext_is_left_alone(app, tmp_path):
    """pv<=7 的第三方指令集社区版可用, 不应被改写/去依赖 (实测: 飞书加急指令 pv7 可用)."""
    _custom_ext(tmp_path / "app" / "xbot_extensions", package_version=7)
    files = PackageBuilder._collect(app)
    package = json.loads((app / "package.json").read_text(encoding="utf-8"))
    report = CustomActivityInliner().transform(app, files, package)
    assert report.converted == []
    assert report.kept and "ERP文件下载" in report.kept[0]
    # 依赖与调用都原样保留
    assert f"{CUSTOM}==26.9.3" in package["internaldependencies"]
    assert "xbot_extensions" in files["main.py"].decode("utf-8")
    assert [f["filename"] for f in package["flows"]] == ["main", "process1"]


def test_force_convert_all_custom_overrides_version_rule(app, tmp_path):
    _custom_ext(tmp_path / "app" / "xbot_extensions", package_version=7)
    files = PackageBuilder._collect(app)
    package = json.loads((app / "package.json").read_text(encoding="utf-8"))
    report = CustomActivityInliner(force_convert_all_custom=True).transform(
        app, files, package)
    assert report.converted
    assert f"{CUSTOM}==26.9.3" not in package["internaldependencies"]


def test_internal_subflow_calls_are_remapped(app):
    """主流程里对子流程的调用必须改写到新流程, 不能指到应用自己的同名流程.

    (曾经的真 bug: 先写主流程、后分配子流程文件名, 导致 process="process1" 没被改写,
    迁移过去的应用里 ERP文件下载 会调用到应用自己的 process1。)
    """
    files = PackageBuilder._collect(app)
    package = json.loads((app / "package.json").read_text(encoding="utf-8"))
    report = CustomActivityInliner().transform(app, files, package)

    names = {f["filename"]: f["name"] for f in package["flows"]}
    new_files = [f for f in names if f.startswith("process") and f != "process1"]
    assert len(new_files) == 2, names          # 主流程 + 子流程
    main_new = next(f for f in new_files if names[f] == "ERP文件下载")
    sub_new = next(f for f in new_files if names[f] == "下载1")

    main_src = files[f"{main_new}.py"].decode("utf-8")
    assert f'process="{sub_new}"' in main_src        # 指向新的子流程
    assert 'process="process1"' not in main_src      # 绝不能残留旧引用
    assert '"下载1"' in main_src                     # 调用的 _block 标题也换了
    # 子流程文件确实存在
    assert f"{sub_new}.py" in files


def test_new_flow_json_points_to_new_subflow(app):
    """新流程的 flow.json 里「调用流程」也必须指向新流程 (设计器看到的要和实际跑的一致)."""
    files = PackageBuilder()._collect(app) if False else PackageBuilder._collect(app)
    package = json.loads((app / "package.json").read_text(encoding="utf-8"))
    CustomActivityInliner().transform(app, files, package)

    names = {f["filename"]: f["name"] for f in package["flows"]}
    main_new = next(k for k, v in names.items() if v == "ERP文件下载")
    sub_new = next(k for k, v in names.items() if v == "下载1")

    flow = json.loads(files[f".dev/{main_new}.flow.json"].decode("utf-8"))
    calls = [b for b in flow["blocks"] if b["name"] == "process.run"]
    assert calls, "主流程里应有调用流程块"
    for block in calls:
        target = block["inputs"]["process"]
        assert target["value"] == f"10:{sub_new}", target   # 不是应用自己的 process1
        assert target["display"] == "下载1", target
    # 子流程的入参也要在参数表里
    sub = json.loads(files[f".dev/{sub_new}.flow.json"].decode("utf-8"))
    assert any(p["name"] == "web_page" for p in sub["parameters"]), sub["parameters"]
