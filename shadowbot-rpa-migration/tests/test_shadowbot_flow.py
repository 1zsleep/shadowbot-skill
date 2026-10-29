"""流程还原编译器测试: 由影刀生成的 .py 还原可视化块."""
from migration_assistant.services.shadowbot_flow import (
    BlockLibrary, FlowCompileError, FlowCompiler)

LIB = BlockLibrary.load()


def _compile(source: str, flow_name: str = "下载1"):
    return FlowCompiler(LIB, flow_name).compile(source)


def test_wait_and_click_blocks_with_selector_refs():
    src = '''import xbot
import xbot_visual
from . import package

def main(args):
    if args is None:
        web_page = None
    else:
        web_page = args.get("web_page", None)
    try:
        web_wait_result = xbot_visual.web.element.wait(browser=web_page, element=package.selector("自动生成报告设置"), state="appear", iswait=True, timeout="60", _block=("下载1", 1, "等待元素(web)"))
        xbot_visual.web.element.click(browser=web_page, element=package.selector("下载"), simulate=True, move_douse=False, delay_after="1", _block=("下载1", 2, "点击元素(web)"))
    finally:
        pass
'''
    flow = _compile(src)
    assert [b["name"] for b in flow.blocks] == ["web.element.wait", "web.element.click"]
    first = flow.blocks[0]
    assert first["inputs"]["element"] == {"value": "12:自动生成报告设置",
                                          "display": "自动生成报告设置"}
    assert first["inputs"]["browser"]["value"] == "11:web_page"
    assert first["outputs"]["wait_result"]["name"] == "web_wait_result"
    assert flow.lossless
    assert [p["name"] for p in flow.parameters] == ["web_page"]


def test_if_region_and_retry_scaffold_are_reconstructed():
    src = '''import xbot, xbot_visual, time
from . import package

def main(args):
    if args is None:
        x = ""
    else:
        x = args.get("x", "")
    try:
        #region 领星导出中心
        if xbot_visual.workflow.test(operand1=x, operator="==", operand2="1", operator_options="{}", _block=("main", 2, "IF 条件")):
            _xbot_retry_time = 0
            while _xbot_retry_time <= 1:
                try:
                    web_page = xbot_visual.web.create(web_type="chrome", value="https://a.example.com", _block=("main", 4, "打开网页"))
                    _xbot_retry_time = 3
                except Exception as e:
                    if _xbot_retry_time == 1:
                        raise e
                    else:
                        xbot_visual.programing.log(type='info', text=e, _block=("main", 4, "打开网页"))
                    _xbot_retry_time += 1
                    time.sleep(1)
            # 说明文字
        #endif
        #endregion
    finally:
        pass
'''
    flow = _compile(src, "main")
    assert [b["name"] for b in flow.blocks] == [
        "programing.region", "workflow.if", "web.create",
        "programing.comment", "workflow.endif", "programing.endregion"]
    assert flow.blocks[0]["inputs"]["content"]["value"] == "10:领星导出中心"
    assert flow.blocks[2]["inputs"]["value"]["value"] == "10:https://a.example.com"
    assert flow.lossless, flow.warnings


def test_try_catch_and_extension_call():
    src = '''import xbot, xbot_visual
from . import package

def main(args):
    try:
        try:
            process_result = xbot_visual.process.run(process="process1", package=__name__, inputs={}, outputs=[], _block=("main", 2, "调用流程"))
        except Exception as exception:
            exception = xbot_visual.trace(exception)
        #endtry
    finally:
        pass
'''
    flow = _compile(src, "main")
    assert [b["name"] for b in flow.blocks] == [
        "programing.try", "process.run", "programing.catch", "programing.endtry"]
    assert flow.blocks[1]["inputs"]["process"]["value"] == "10:process1"


def test_unknown_statement_is_reported_not_silently_dropped():
    src = '''import xbot, xbot_visual
from . import package

def main(args):
    try:
        import pandas as pd
    finally:
        pass
'''
    flow = _compile(src, "main")
    assert flow.blocks == []
    assert not flow.lossless
    assert any("pandas" in w for w in flow.warnings)


def test_condition_without_block_is_rejected():
    src = '''def main(args):
    if 1 == 1:
        pass
'''
    try:
        _compile(src, "main")
    except FlowCompileError as exc:
        assert "条件" in str(exc)
    else:
        raise AssertionError("应当拒绝非块条件")


def test_parameters_found_when_locals_declared_before_args_prologue():
    """指令集流程常先声明局部变量再进 if args is None —— 入参也必须被收集到.

    (曾经的真 bug: 取参段必须是函数体第一条语句, 否则直接 break,
     导致「下载1」这类流程的入参 web_page 丢失。)
    """
    src = '''import xbot
import xbot_visual
from . import package


def main(args):
    web_wait_result = False
    if args is None:
        web_page = None
    else:
        web_page = args.get("web_page", None)
    try:
        web_wait_result = xbot_visual.web.element.wait(browser=web_page, element=package.selector("下载"), state="appear", iswait=True, timeout="60", _block=("下载1", 1, "等待元素(web)"))
    finally:
        args["web_wait_result"] = web_wait_result


'''
    flow = _compile(src)
    got = {(p["name"], p["direction"]) for p in flow.parameters}
    assert ("web_page", "In") in got
    assert ("web_wait_result", "Out") in got
