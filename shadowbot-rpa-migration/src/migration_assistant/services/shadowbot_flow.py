"""影刀流程文件: 由生成的 <flow>.py 还原可视化块, 产出 .dev/<flow>.flow.json.

影刀应用的运行靠 `xbot_robot/<flow>.py`, 设计器靠 `xbot_robot/.dev/<flow>.flow.json`。
把「自定义指令集」搬成应用内流程时, 指令集只带生成好的 .py, 因此需要反过来还原成块。

还原依据 (均由本机真实流程实测得出):
  * `xbot_visual.<a>.<b>(..., _block=("<流程名>", <序号>, "<标题>"))` 对应一个块, 块名 = `a.b`;
    调用扩展指令时块名是 `xbot_extensions.<code>.<flow>`, 实参在 `inputs={...}` 里, 并带 `block_title`;
  * 实参编码: 字符串/数字 `10:`、bool `13:`、None → null、变量/属性/下标/其它调用 `11:`、
    lambda → `13:<表达式>`、`package.selector("X")` → `12:X`、`package.image_selector("X")` → `14:X`、
    字符串拼接 → `15:[段...]`、字典 → `16:[{...}]`;
  * 容器块由结构决定: if→endif、循环→endloop、try→catch/endtry、else/elseif、break;
  * 注释类块由源码注释还原: `#region X`→programing.region、`# <文本>`→programing.comment、
    `#endregion`→programing.endregion;
  * 块级「失败重试」会生成 `while _xbot_retry_time <= n:` 脚手架, 需还原成被包裹的那个块。
"""
from __future__ import annotations

import ast
import json
import uuid
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

_SELECTOR_CALLS = {"package.selector": "12", "package.image_selector": "14"}
_RETRY_VAR = "_xbot_retry_time"
_LOOP_OPENERS = {"workflow.forin", "workflow.infinite_loop", "workflow.forin_expr",
                 "workflow.loop_dict", "workflow.while"}
_CONTAINER_CLOSER = {
    "workflow.if": "workflow.endif",
    "workflow.elseif": "workflow.endif",
    "workflow.multiple_conditions_if": "workflow.endif",
    "workflow.multiple_conditions_elseif": "workflow.endif",
    "programing.try": "programing.endtry",
}


class FlowCompileError(Exception):
    """无法把某段生成代码还原成可视化块 (附带原因, 便于提示用户)."""


# ---------------------------------------------------------------- 指令库

@dataclass
class BlockLibrary:
    blocks: dict[str, dict] = field(default_factory=dict)
    functions: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "BlockLibrary":
        if path is None:
            text = (resources.files("migration_assistant.resources")
                    .joinpath("shadowbot_blocklib.json").read_text(encoding="utf-8"))
        else:
            text = Path(path).read_text(encoding="utf-8")
        data = json.loads(text)
        return cls(blocks=dict(data["byBlock"]), functions=dict(data["functionToBlock"]))

    def block(self, name: str) -> dict | None:
        return self.blocks.get(name)

    def block_for_function(self, function: str) -> dict | None:
        name = self.functions.get(function)
        return self.blocks.get(name) if name else None

    def extend(self, blocks: list[dict]) -> None:
        """并入扩展指令集自带的块定义 (prototype.block.json)."""
        for blk in blocks:
            name = blk.get("name")
            if not name or name in self.blocks:
                continue
            self.blocks[name] = {
                "name": name,
                "function": blk.get("function"),
                "inputs": [_compact_input(i) for i in (blk.get("inputs") or [])],
                "outputs": [_compact_output(o) for o in (blk.get("outputs") or [])],
                "isCondition": False,
                "isLoop": False,
                "extension": blk.get("extension"),
                "title": blk.get("title"),
            }


def _compact_input(inp: dict) -> dict:
    out = {"name": inp.get("name"), "type": inp.get("type")}
    if inp.get("default") is not None:
        out["default"] = inp["default"]
    if inp.get("required"):
        out["required"] = True
    options = ((inp.get("editor") or {}).get("options")) or inp.get("options")
    if options:
        out["options"] = [{"value": o.get("value"), "display": o.get("display")}
                          for o in options if isinstance(o, dict) and o.get("value") is not None]
    return out


def _compact_output(out: dict) -> dict:
    return {"id": out.get("id") or out.get("name"), "name": out.get("name"),
            "type": out.get("type")}


def load_extension_blocks(app_dir: str | Path, library: BlockLibrary) -> dict[str, str]:
    """把应用 xbot_extensions 里所有指令集的块定义并入 library, 返回 code→指令集名."""
    names: dict[str, str] = {}
    ext_root = Path(app_dir) / "xbot_extensions"
    if not ext_root.is_dir():
        return names
    for code_dir in sorted(p for p in ext_root.iterdir() if p.is_dir()):
        pkg_file = code_dir / "package.json"
        proto_file = code_dir / "prototype.block.json"
        if not pkg_file.is_file():
            continue
        try:
            pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
        except ValueError:
            continue
        code = pkg.get("activity_code") or code_dir.name
        names[code] = pkg.get("name") or code
        if proto_file.is_file():
            try:
                proto = json.loads(proto_file.read_text(encoding="utf-8"))
            except ValueError:
                continue
            library.extend(proto.get("blocks") or [])
    return names


@dataclass
class CompiledFlow:
    name: str
    blocks: list[dict] = field(default_factory=list)
    parameters: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def lossless(self) -> bool:
        """是否完整还原 (有告警说明有语句/块没还原出来, 此时不可用于替换原流程)."""
        return not self.warnings


# ---------------------------------------------------------------- 编译器

class FlowCompiler:
    def __init__(self, library: BlockLibrary, flow_name: str,
                 flow_names: dict[str, str] | None = None,
                 extension_names: dict[str, str] | None = None):
        self.library = library
        self.flow_name = flow_name
        self.flow_names = flow_names or {}             # filename -> 流程显示名
        self.extension_names = extension_names or {}   # activity_code -> 指令集名
        self.flow = CompiledFlow(name=flow_name)
        self._comments: list[tuple[int, str, str]] = []
        self._closers: list[tuple[int, str]] = []

    # ------------------------------------------------------------ 对外
    def compile(self, source: str) -> CompiledFlow:
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            raise FlowCompileError(f"流程代码语法错误: {exc}") from exc
        self._comments, self._closers = _scan_comments(source)
        main = _find_main(tree)
        if main is None:
            raise FlowCompileError("找不到 main(args) 函数")
        self.flow.parameters = _collect_parameters(main)
        self._walk_body(_strip_args_prologue(main.body))
        self._flush_comments(10 ** 9)
        for lineno, kind in self._closers:
            self.flow.warnings.append(f"多余的结束标记 #{kind} (第 {lineno} 行)")
        return self.flow

    # ------------------------------------------------------------ 注释块
    def _flush_comments(self, limit: int) -> None:
        while self._comments and self._comments[0][0] < limit:
            _, kind, text = self._comments.pop(0)
            if kind == "region":
                self._emit("programing.region", {"content": {"value": "10:" + text}}, {})
            elif kind == "endregion":
                self._emit("programing.endregion", {}, {})
            elif kind == "comment":
                self._emit("programing.comment", {"content": {"value": "10:" + text}},
                           {}, expanded=True)
            else:
                self.flow.warnings.append(f"暂不支持注释标记: {text[:40]}")

    def _walk_body(self, stmts: list[ast.stmt]) -> None:
        for stmt in stmts:
            self._flush_comments(stmt.lineno)
            self._stmt(stmt)

    def _close(self, kind: str) -> None:
        """消费结构结束标记: 先把归属该容器体的尾部注释吐出, 再补结束块."""
        for i, (lineno, k) in enumerate(self._closers):
            if k == kind:
                self._flush_comments(lineno)
                del self._closers[i]
                break
        self._emit(_CLOSER_BLOCK[kind], {}, {})

    # ------------------------------------------------------------ 语句
    def _stmt(self, node: ast.stmt) -> None:
        if isinstance(node, ast.Try):
            self._try(node)
        elif isinstance(node, ast.If):
            self._if(node)
        elif isinstance(node, (ast.For, ast.While)):
            self._loop(node)
        elif isinstance(node, ast.Assign):
            self._call_block(node.value, _targets(node.targets))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            self._call_block(node.value, _targets([node.target]))
        elif isinstance(node, ast.Expr):
            self._call_block(node.value, [])
        elif isinstance(node, (ast.Return, ast.Pass)):
            return
        elif isinstance(node, ast.Break):
            self._emit("workflow.break", {}, {})
        else:
            text = _unparse(node).strip()
            if text:
                self.flow.warnings.append(f"跳过未识别语句: {text[:60]}")

    def _try(self, node: ast.Try) -> None:
        if not node.handlers:
            self._walk_body(node.body)
            return
        self._emit("programing.try", {}, {}, container=True)
        self._walk_body(node.body)
        handler = node.handlers[0]
        var = getattr(handler.name, "id", None) or "exception"
        self._emit("programing.catch", {},
                   {"exception": {"name": var, "isEnable": True}}, container=True)
        self._walk_body([s for s in handler.body if not _is_trace_assign(s)])
        self._close("endtry")

    def _if(self, node: ast.If, *, elseif: bool = False) -> None:
        block = self._block_of_call(node.test)
        if block is None:
            raise FlowCompileError(f"条件不是影刀指令块: {_unparse(node.test)[:60]}")
        self._emit_block(block, node.test, [],
                         override_name="workflow.elseif" if elseif else None)
        self._walk_body(node.body)
        orelse = node.orelse
        if len(orelse) == 1 and isinstance(orelse[0], ast.If):
            self._if(orelse[0], elseif=True)
            return
        if orelse:
            self._emit("workflow.else", {}, {}, container=True)
            self._walk_body(orelse)
        self._close("endif")

    def _loop(self, node: ast.stmt) -> None:
        if _is_retry_loop(node):
            for s in node.body:
                if isinstance(s, ast.Try):
                    self._walk_body([i for i in s.body if not _is_retry_marker(i)])
                    continue
                if not _is_retry_marker(s):
                    self._stmt(s)
            return
        call = _unwrap_enumerate(node.iter) if isinstance(node, ast.For) else None
        block = self.library.block("workflow.infinite_loop") if call is None \
            else self._block_of_call(call)
        if block is None:
            raise FlowCompileError(f"循环不是影刀指令块: {_unparse(node)[:60]}")
        targets = _targets([node.target]) if isinstance(node, ast.For) else []
        self._emit_block(block, call, targets)
        body = list(node.body)
        if body and _is_loop_index_increment(body[0]):
            body = body[1:]
        self._walk_body(body)
        self._close("endloop")

    # ------------------------------------------------------------ 块
    def _call_block(self, node: ast.AST, targets: list[ast.AST]) -> bool:
        block = self._block_of_call(node)
        if block is None:
            return False
        self._emit_block(block, node, targets)
        return True

    def _block_of_call(self, node: ast.AST | None) -> dict | None:
        if not isinstance(node, ast.Call):
            return None
        fn = _unparse(node.func)
        if not fn.startswith("xbot_visual."):
            return None
        if fn == "xbot_visual.process.run":
            target = _static_kwarg(node, "process")
            if isinstance(target, str) and target.startswith("xbot_extensions."):
                return self.library.block(target)
        block = self.library.block_for_function(fn)
        if block is None:
            # 部分块(process.run / programing.variable 等)在指令库里 function 为空, 用名字兜底
            block = self.library.block(fn[len("xbot_visual."):])
        return block

    def _emit_block(self, block: dict, call: ast.Call | None, targets: list[ast.AST],
                    *, override_name: str | None = None) -> None:
        name = override_name or block["name"]
        kwargs = ({kw.arg: kw.value for kw in call.keywords if kw.arg} if call else {})
        block_title = None
        if name.startswith("xbot_extensions."):
            inputs = self._encode_extension_inputs(block, kwargs)
            outputs = self._encode_extension_outputs(block, kwargs)
            title = block.get("title") or name.rsplit(".", 1)[-1]
            ext = self.extension_names.get(name.split(".")[1], "")
            block_title = f"{ext}/{title}" if ext else title
        elif name in ("process.run", "workflow.elseif"):
            inputs = self._encode_process_run(kwargs)
            outputs = ({"process_result": {"name": getattr(targets[0], "id", "process_result"),
                                           "isEnable": False}} if targets else {})
        else:
            inputs = self._encode_kwargs(block, kwargs, name)
            outputs = self._encode_outputs(block, name, targets)
        self._emit(name, inputs, outputs,
                   container=bool(block.get("isLoop") or block.get("isCondition")
                                  or name in _CONTAINER_CLOSER),
                   block_title=block_title)

    def _encode_kwargs(self, block: dict, kwargs: dict[str, ast.AST],
                       name: str = "") -> dict:
        inputs = {}
        for idef in block.get("inputs") or []:
            node = kwargs.get(idef["name"])
            if node is None:
                if idef.get("required") and "default" in idef:
                    inputs[idef["name"]] = {"value": idef["default"]}
                continue
            inputs[idef["name"]] = _encode_value(node, idef)
        if name == "programing.variable":
            value = kwargs.get("value")
            inputs["value_type"] = {"value": "10:" + _infer_value_type(value)}
        return inputs

    def _encode_extension_inputs(self, block: dict, kwargs: dict[str, ast.AST]) -> dict:
        """扩展指令: 实参在 process.run 的 inputs 字典里."""
        call_inputs = kwargs.get("inputs")
        if not isinstance(call_inputs, ast.Dict):
            return {}
        defs = {i["name"]: i for i in block.get("inputs") or []}
        inputs = {}
        for key, value in zip(call_inputs.keys, call_inputs.values):
            if not isinstance(key, ast.Constant):
                continue
            inputs[key.value] = _encode_value(value, defs.get(key.value) or {})
        return inputs

    def _encode_extension_outputs(self, block: dict, kwargs: dict[str, ast.AST]) -> dict:
        outputs_node = kwargs.get("outputs")
        if not isinstance(outputs_node, (ast.List, ast.Tuple)):
            return {}
        out_defs = [o for o in (block.get("outputs") or [])]
        outputs = {}
        for odef, item in zip(out_defs, outputs_node.elts):
            if isinstance(item, ast.Constant):
                outputs[odef["id"]] = {"name": item.value, "isEnable": True}
        return outputs

    def _encode_process_run(self, kwargs: dict[str, ast.AST]) -> dict:
        inputs: dict = {}
        target = _static_kwarg_node(kwargs.get("process"))
        if target is not None:
            inputs["process"] = {"value": f"10:{target.value}"}
            display = self.flow_names.get(target.value)
            if display:
                inputs["process"]["display"] = display
        if "package" in kwargs:
            inputs["package"] = _encode_value(kwargs["package"], {"type": "any"})
        if "inputs" in kwargs:
            inputs["inputs"] = _encode_value(kwargs["inputs"], {"type": "str"})
        if "outputs" in kwargs and isinstance(kwargs["outputs"], (ast.List, ast.Tuple)):
            inputs["outputs"] = {"value": "10:" + json.dumps(
                [{"name": e.value, "type": "str"} for e in kwargs["outputs"].elts
                 if isinstance(e, ast.Constant)], ensure_ascii=False)}
        return inputs

    def _encode_outputs(self, block: dict, name: str, targets: list[ast.AST]) -> dict:
        if not targets:
            return {}
        out_defs = block.get("outputs") or []
        if name in _LOOP_OPENERS and out_defs:
            by_id = {o["id"]: o for o in out_defs}
            order = ["loop_index", "loop_item"] if len(targets) >= 2 else ["loop_item"]
            outputs = {}
            for oid, target in zip(order, targets):
                odef = by_id.get(oid)
                if odef is None:
                    continue
                outputs[oid] = {"name": getattr(target, "id", _unparse(target)),
                                "type": odef.get("type") or "any", "isEnable": True}
            return outputs
        outputs = {}
        for odef, target in zip(out_defs, targets):
            entry = {"name": getattr(target, "id", _unparse(target)), "isEnable": True}
            if odef.get("type"):
                entry["type"] = odef["type"]
            outputs[odef["id"]] = entry
        return outputs

    def _emit(self, name: str, inputs: dict, outputs: dict, *,
              container: bool = False, block_title: str | None = None,
              expanded: bool = False) -> None:
        block = {"id": str(uuid.uuid4()), "name": name, "isEnabled": True,
                 "inputs": inputs, "outputs": outputs}
        if container:
            block["foldState"] = "UnFold"
        if block_title:
            block["block_title"] = block_title
        if expanded:
            block["isCommentBlockExpanded"] = True
        self.flow.blocks.append(block)


# ---------------------------------------------------------------- 值编码

def _encode_value(node: ast.AST, idef: dict) -> dict:
    out: dict = {"value": _encode_raw(node)}
    display = _display_of(node, idef)
    if display is not None:
        out["display"] = display
    return out


def _encode_raw(node: ast.AST):
    if isinstance(node, ast.Lambda):
        return "13:" + _unparse(node.body).strip()
    if isinstance(node, ast.Constant):
        v = node.value
        if v is None:
            return None
        if isinstance(v, bool):
            return f"13:{v}"
        return f"10:{v}"
    if isinstance(node, ast.Call):
        fn = _unparse(node.func)
        if fn in _SELECTOR_CALLS and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return f"{_SELECTOR_CALLS[fn]}:{arg.value}"
        return "11:" + _unparse(node)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return "15:" + json.dumps(_segments(node), ensure_ascii=False,
                                     separators=(",", ":"))
    if isinstance(node, ast.Dict):
        return "16:" + json.dumps(
            [{k.value: _encode_raw(v)} for k, v in zip(node.keys, node.values)
             if isinstance(k, ast.Constant)], ensure_ascii=False, separators=(",", ":"))
    if isinstance(node, (ast.List, ast.Tuple)):
        if _is_image_selector_list(node):
            return "14:" + node.elts[0].args[0].value
        return "16:" + json.dumps([_encode_raw(e) for e in node.elts],
                                      ensure_ascii=False, separators=(",", ":"))
    return "11:" + _unparse(node)


def _display_of(node: ast.AST, idef: dict) -> str | None:
    if isinstance(node, ast.Call):
        fn = _unparse(node.func)
        if fn in _SELECTOR_CALLS and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return arg.value
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        for opt in idef.get("options") or []:
            if opt.get("value") == node.value:
                return opt.get("display")
    return None


def _infer_value_type(node: ast.AST | None) -> str:
    raw = _encode_raw(node) if node is not None else None
    if isinstance(node, ast.Lambda):
        node = node.body
        if isinstance(node, (ast.List, ast.ListComp)):
            return "list"
        if isinstance(node, ast.Dict):
            return "dict"
        text = _unparse(node)
        if text.startswith(("int(", "float(")):
            return "int"
    if isinstance(raw, str):
        if raw.startswith("13:["):
            return "list"
        if raw.startswith("13:{"):
            return "dict"
        if raw.startswith("13:True") or raw.startswith("13:False"):
            return "bool"
    return "str"


def _segments(node: ast.AST) -> list[str]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _segments(node.left) + _segments(node.right)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return ["10:" + node.value]
    if isinstance(node, ast.Call) and _unparse(node.func) in ("xbot_visual.sh_str", "sh_str"):
        return ["11:" + _unparse(node.args[0])] if node.args else []
    return ["11:" + _unparse(node)]


def _is_image_selector_list(node: ast.AST) -> bool:
    return (isinstance(node, (ast.List, ast.Tuple)) and len(node.elts) == 1
            and isinstance(node.elts[0], ast.Call)
            and _unparse(node.elts[0].func) == "package.image_selector"
            and node.elts[0].args
            and isinstance(node.elts[0].args[0], ast.Constant))


# ---------------------------------------------------------------- 源码扫描/辅助

_CLOSER_MARKERS = {"endif", "endloop", "endtry", "endfile"}
_CLOSER_BLOCK = {"endif": "workflow.endif", "endloop": "workflow.endloop",
                  "endtry": "programing.endtry"}


def _scan_comments(source: str) -> tuple[list[tuple[int, str, str]], list[tuple[int, str]]]:
    """收集注释行 → (可还原为块的注释, 结构结束标记).

    结构结束标记 (`#endif`/`#endloop`/`#endtry`) 本身不是块, 但它的行号能界定
    "这段容器体内末尾的注释" 归属, 否则注释会被排到容器后面去。
    """
    blocks: list[tuple[int, str, str]] = []
    closers: list[tuple[int, str]] = []
    for lineno, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        body = stripped.lstrip("#").strip()
        if not body:
            continue
        head = body.split(" ", 1)[0]
        if head in _CLOSER_MARKERS:
            closers.append((lineno, head))
            continue
        if head == "endregion":
            blocks.append((lineno, "endregion", ""))
            continue
        if body.startswith("region "):
            blocks.append((lineno, "region", body[len("region "):].strip()))
        elif body.startswith(("programing.", "workflow.", "process.", "xbot_extensions.")):
            blocks.append((lineno, "unsupported", body))
        else:
            blocks.append((lineno, "comment", body))
    return blocks, closers


def _unparse(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover
        return ""


def _targets(nodes: list[ast.AST]) -> list[ast.AST]:
    flat: list[ast.AST] = []
    for n in nodes:
        if isinstance(n, ast.Tuple):
            flat.extend(n.elts)
        else:
            flat.append(n)
    return flat


def _unwrap_enumerate(node: ast.AST) -> ast.AST:
    if isinstance(node, ast.Call) and _unparse(node.func) == "enumerate" and node.args:
        return node.args[0]
    return node


def _static_kwarg(call: ast.Call, name: str):
    node = next((kw.value for kw in call.keywords if kw.arg == name), None)
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _static_kwarg_node(node: ast.AST | None):
    return node if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _find_main(tree: ast.Module) -> ast.FunctionDef | None:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    return None


def _strip_args_prologue(body: list[ast.stmt]) -> list[ast.stmt]:
    """跳过取参段 (前面可能还有 `X = None` 之类变量声明)."""
    for idx, node in enumerate(body):
        if isinstance(node, ast.If) and _mentions_args(node.test):
            return body[idx + 1:]
    return body


def _mentions_args(node: ast.AST) -> bool:
    return any(isinstance(n, ast.Name) and n.id == "args" for n in ast.walk(node))


def _is_trace_assign(node: ast.stmt) -> bool:
    return (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
            and _unparse(node.value.func) == "xbot_visual.trace")


def _is_retry_loop(node: ast.stmt) -> bool:
    return (isinstance(node, ast.While)
            and any(isinstance(n, ast.Name) and n.id == _RETRY_VAR
                    for n in ast.walk(node.test)))


def _is_retry_marker(node: ast.stmt) -> bool:
    if isinstance(node, ast.Assign):
        if any(isinstance(t, ast.Name) and t.id == _RETRY_VAR for t in node.targets):
            return True
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        return _unparse(node.value.func) == "time.sleep"
    return False


def _is_loop_index_increment(node: ast.stmt) -> bool:
    if not isinstance(node, (ast.AugAssign, ast.Assign)):
        return False
    target = node.target if isinstance(node, ast.AugAssign) else (
        node.targets[0] if node.targets else None)
    return isinstance(target, ast.Name) and target.id.startswith("loop_index")


def _collect_parameters(main: ast.FunctionDef) -> list[dict]:
    params: dict[tuple[str, str], dict] = {}
    for node in main.body:
        # 取参段不一定是函数体第一条语句 (指令集的流程常先声明几个局部变量, 如
        # `web_wait_result = False` 再进 if args is None), 所以不能 break, 要往后找。
        if not isinstance(node, ast.If) or not _mentions_args(node.test):
            continue
        for branch in (node.body, node.orelse):
            for stmt in branch:
                if not isinstance(stmt, ast.Assign) or not isinstance(stmt.value, ast.Call):
                    continue
                if not _unparse(stmt.value.func).endswith("args.get"):
                    continue
                default = stmt.value.args[1] if len(stmt.value.args) > 1 else None
                for t in stmt.targets:
                    if isinstance(t, ast.Name):
                        params[("In", t.id)] = _param(t.id, getattr(default, "value", ""))
    for node in ast.walk(main):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Subscript) and _unparse(t.value) == "args":
                    key = t.slice
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        p = _param(key.value, "")
                        p["direction"] = "Out"
                        params[("Out", key.value)] = p
    return list(params.values())


def _param(name: str, value) -> dict:
    if isinstance(value, bool) or isinstance(value, (int, float)):
        kind, text = "Text", str(value)
    elif isinstance(value, (list, dict)):
        kind, text = "Expression", json.dumps(value, ensure_ascii=False)
    else:
        kind, text = "Expression", ("None" if value is None else str(value))
    return {"name": name, "direction": "In", "type": "str",
            "value": text, "description": "", "kind": kind}


def write_flow_json(flow: CompiledFlow, path: str | Path) -> str:
    payload = {"name": flow.name, "memo": "", "kind": "visual",
               "blocks": flow.blocks, "parameters": flow.parameters}
    text = json.dumps(payload, ensure_ascii=False, indent=2).replace("\n", "\r\n")
    Path(path).write_text(text, encoding="utf-8")
    return text
