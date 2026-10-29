"""指令集「使用中」判定: 注释和被禁用的积木都不算使用.

回归背景: 主应用把「示例部门--示例指令集」这块积木禁用了 —— .py 里只剩一行注释,
.flow.json 里保留 isEnabled=false 的节点。旧逻辑把两者都当成"在用", 于是去内联
这个根本没跑的指令集; 内联失败后依赖声明原样保留, 服务端报
「社区版或基础账号不能使用高级指令集」, 迁移直接失败。
"""
import json
from pathlib import Path

from migration_assistant.services.custom_activity_inliner import (
    CustomActivityInliner, _Extension, _enabled_refs_from_flow_json, _strip_comments)


def _ext(code="activity_x", pv=8, official=False):
    return _Extension(code=code, name="X", version="1", official=official,
                      dir=Path("."), package={}, flow_titles={}, proto_blocks={},
                      package_version=pv)


# ---------- 注释剔除 ----------

def test_strip_comments_drops_full_line_and_trailing_comment():
    src = ("a = 1  # xbot_extensions.activity_x.main\n"
           "# xbot_extensions.activity_y.main\n")
    out = _strip_comments(src)
    assert "activity_x" not in out
    assert "activity_y" not in out


def test_strip_comments_keeps_hash_inside_string():
    src = "url = 'https://x/#anchor'  # real comment\n"
    out = _strip_comments(src)
    assert "#anchor" in out
    assert "real comment" not in out


# ---------- flow.json 的启用状态 ----------

def test_flow_json_skips_disabled_block():
    payload = {"blocks": [
        {"name": "xbot_extensions.activity_x.main", "isEnabled": False},
        {"name": "xbot_extensions.activity_y.main", "isEnabled": True},
        {"name": "workflow.if"},
    ]}
    refs = _enabled_refs_from_flow_json(json.dumps(payload))
    assert ("activity_y", "main") in refs
    assert ("activity_x", "main") not in refs


def test_flow_json_without_isEnabled_counts_as_enabled():
    """没有 isEnabled 字段的节点默认是启用的, 不能漏判."""
    payload = {"blocks": [{"name": "xbot_extensions.activity_x.main"}]}
    assert ("activity_x", "main") in _enabled_refs_from_flow_json(json.dumps(payload))


# ---------- 整体判定 ----------

def test_used_flows_ignores_commented_call():
    files = {"main.py": b"# xbot_extensions.activity_x.main\nprint(1)\n"}
    assert CustomActivityInliner._used_flows(files, {"activity_x": _ext()}) == {}


def test_used_flows_ignores_disabled_flow_json_block():
    payload = {"blocks": [{"name": "xbot_extensions.activity_x.main", "isEnabled": False}]}
    files = {".dev/main.flow.json": json.dumps(payload).encode()}
    assert CustomActivityInliner._used_flows(files, {"activity_x": _ext()}) == {}


def test_used_flows_counts_real_enabled_call():
    payload = {"blocks": [{"name": "xbot_extensions.activity_x.main", "isEnabled": True}]}
    files = {
        ".dev/main.flow.json": json.dumps(payload).encode(),
        "main.py": (b"x = xbot_visual.process.run("
                    b"process='xbot_extensions.activity_x.main', package=__name__)\n"),
    }
    assert CustomActivityInliner._used_flows(files, {"activity_x": _ext()}) == {
        "activity_x": {"main"}}
