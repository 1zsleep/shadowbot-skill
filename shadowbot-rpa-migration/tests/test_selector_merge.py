"""元素合并: 判重必须认 <selector> 标签, 不能被定位链里的同名属性骗过.

回归背景: 应用的 selectorsV2.xml 里有 `acc-name="下载"`(Chrome 下载页节点属性),
旧逻辑用 `f'name="{n}"' in xml` 做子串判断, 把 ERP 指令集的「下载」元素当成
"已存在" 直接跳过合并 —— 迁移后设计器里就没有这个元素了。
"""
from pathlib import Path

from migration_assistant.services.custom_activity_inliner import (
    CustomActivityInliner, _Extension, _selector_ids_by_name)

SEL_ID = "sel-1"


def _inliner():
    # 跳过 __init__ 的 BlockLibrary 加载, 这里只测元素合并
    return CustomActivityInliner.__new__(CustomActivityInliner)


def _make_ext(tmp_path: Path, name="下载", flow="process1") -> _Extension:
    d = tmp_path / "activity_x"
    d.mkdir()
    (d / f"{flow}.py").write_text(
        f'x = package.selector("{name}")', encoding="utf-8")
    (d / "selectorsV2.xml").write_text(
        '<repository><group id="g1" name="领星ERP">'
        f'<selector id="{SEL_ID}" name="{name}"><web x:name="div"/></selector>'
        "</group></repository>", encoding="utf-8")
    return _Extension(code="activity_x", name="ERP文件下载", version="1",
                      official=False, dir=d, package={}, flow_titles={},
                      proto_blocks={}, package_version=8)


def test_merge_ignores_same_name_in_element_path_attributes(tmp_path):
    ext = _make_ext(tmp_path)
    app_xml = ('<repository><group id="g"><web x:name="Document" '
               'acc-name="下载" value="edge://downloads-hub/"/></group></repository>')
    files = {"selectorsV2.xml": app_xml.encode()}
    plan = {("activity_x", "process1"): ("process9", "下载1")}

    conflicts = _inliner()._merge_selectors(files, {"activity_x": ext}, plan)

    assert conflicts == []
    merged = files["selectorsV2.xml"].decode()
    assert f'<selector id="{SEL_ID}" name="下载"' in merged
    assert "自定义指令内联元素" in merged


def test_merge_skips_when_identical_selector_already_present(tmp_path):
    ext = _make_ext(tmp_path)
    app_xml = ("<repository><group id=\"g\">"
               f'<selector id="{SEL_ID}" name="下载"><web x:name="div"/></selector>'
               "</group></repository>")
    files = {"selectorsV2.xml": app_xml.encode()}
    plan = {("activity_x", "process1"): ("process9", "下载1")}

    conflicts = _inliner()._merge_selectors(files, {"activity_x": ext}, plan)

    assert conflicts == []
    assert files["selectorsV2.xml"].decode() == app_xml     # 没重复插入


def test_merge_reports_conflict_for_same_name_different_selector(tmp_path):
    """同名但定义不同 -> 既不合并也不静默通过, 交给人工判断."""
    ext = _make_ext(tmp_path)
    app_xml = ("<repository><group id=\"g\">"
               '<selector id="app-other" name="下载"><web x:name="div"/></selector>'
               "</group></repository>")
    files = {"selectorsV2.xml": app_xml.encode()}
    plan = {("activity_x", "process1"): ("process9", "下载1")}

    conflicts = _inliner()._merge_selectors(files, {"activity_x": ext}, plan)

    assert conflicts == ["下载"]
    assert files["selectorsV2.xml"].decode() == app_xml


def test_selector_ids_by_name_reads_only_selector_tags():
    xml = ('<repository><group id="g">'
           '<selector id="a" name="下载"><web/></selector>'
           '<web x:name="d" acc-name="下载"/>'
           '<web x:name="e" uia-name="下载"/>'
           "</group></repository>")
    ids = _selector_ids_by_name(xml)
    assert ids == {"下载": {"a"}}
