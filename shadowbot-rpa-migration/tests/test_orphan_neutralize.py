"""孤儿积木处理: 依赖被移除后, 流程里残留的禁用积木要变成注释块.

背景: 目标账号没有该指令集, 原节点在设计器里渲染不出来, 会被降级成未知积木 ——
用户看到的就是"原本禁用的积木变成启用了"。换成标准注释块后既不执行, 又能正常显示。
"""
import json
from pathlib import Path

from migration_assistant.services.custom_activity_inliner import CustomActivityInliner, _Extension


def _ext(code="activity_x", name="X/主流程"):
    return _Extension(code=code, name=name, version="1", official=False,
                      dir=Path("."), package={}, flow_titles={}, proto_blocks={},
                      package_version=8)


# ---------- .py ----------

def test_neutralize_py_handles_crlf_and_keeps_line_endings():
    """影刀流程文件是 CRLF, 替换后不能把该行变成 LF."""
    text = "a = 1\r\n    # xbot_extensions.activity_x.main\r\nb = 2\r\n"
    out = CustomActivityInliner._neutralize_py(text, {"activity_x"},
                                               {"activity_x": _ext()})
    assert "activity_x" not in out
    assert "未迁移" in out
    assert out.count("\r\n") == 3


def test_neutralize_py_leaves_other_instructions_untouched():
    text = "# xbot_extensions.activity_other.main\n"
    out = CustomActivityInliner._neutralize_py(text, {"activity_x"},
                                               {"activity_x": _ext()})
    assert out == text


# ---------- .flow.json ----------

def test_neutralize_flow_json_replaces_only_disabled_orphans():
    payload = {"blocks": [
        {"id": "1", "name": "xbot_extensions.activity_x.main", "isEnabled": False,
         "block_title": "X/主流程"},
        {"id": "2", "name": "xbot_extensions.activity_x.other", "isEnabled": True},
        {"id": "3", "name": "workflow.if", "isEnabled": False},
    ]}
    key = ".dev/main.flow.json"
    files = {key: json.dumps(payload).encode()}

    CustomActivityInliner._neutralize_flow_json(files, key, {"activity_x"},
                                                {"activity_x": _ext()})

    blocks = json.loads(files[key].decode())["blocks"]
    assert blocks[0]["name"] == "programing.comment"
    assert blocks[0]["isEnabled"] is True
    assert "未迁移" in blocks[0]["inputs"]["content"]["value"]
    assert "X/主流程" in blocks[0]["inputs"]["content"]["value"]
    # 启用中的同名指令集节点走内联改写, 不该被这里的逻辑碰
    assert blocks[1]["name"] == "xbot_extensions.activity_x.other"
    # 普通禁用积木(非扩展)保持原样
    assert blocks[2]["name"] == "workflow.if"
    assert blocks[2]["isEnabled"] is False


def test_neutralize_skips_when_nothing_dropped():
    key = ".dev/main.flow.json"
    raw = json.dumps({"blocks": [
        {"id": "1", "name": "xbot_extensions.activity_x.main", "isEnabled": False}]}
    ).encode()
    files = {key: raw}
    CustomActivityInliner()._neutralize_orphans(files, {"activity_x": _ext()}, [])
    assert files[key] == raw
