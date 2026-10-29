"""把应用用到的「第三方/自定义指令集」内联成应用内流程.

背景
----
影刀对非官方市场(第三方/自定义)指令集有账号等级限制: 社区版/基础账号在同步/发版时会报
「社区版或基础账号不能使用高级指令集」。迁移到这类账号时, 打包上传的内容是 `xbot_robot/**`
(不含 `xbot_extensions/`), 服务端按 package.json 的 `internaldependencies` /
`internalautodependencies` 声明判定, 因此只要把用到的自定义指令集**换成应用自己的流程**
并同步依赖声明, 就能通过校验。

本模块在打包时(**内存中改写, 不碰磁盘源文件**)做等价替换:

  1. 从 `xbot_extensions/` 找出非官方的指令集, 且只处理**流程里真正调用过**的流程;
  2. 把每个被调用的指令集流程搬成应用自己的流程(新 `processN`):
     - `<flow>.py` 直接用指令集自带的生成代码(运行行为 1:1), 仅改流程名与内部调用指向;
     - `.dev/<flow>.flow.json` 由 shadowbot_flow 从该 .py 反向还原(供设计器显示);
     - 指令集自带的网页元素(选择器)并进应用元素库;
  3. 应用里对扩展指令的调用改成调用新的应用内流程(`.py` 与 `.flow.json` 同步改);
  4. 从 package.json 依赖声明里去掉这些指令集。

**无损原则**: 某流程若无法完整还原成可视化块(例如含 `programing.snippet` 代码块), 则该指令集
**既不转换也不动依赖**, 只记告警交回人工 —— 避免出现"缺块的 flow.json, 用户一保存就把代码冲掉"。
"""
from __future__ import annotations

import io
import json
import re
import tokenize
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from migration_assistant.services.shadowbot_flow import (
    BlockLibrary, FlowCompileError, FlowCompiler)

_OFFICIAL_ICON_HOSTS = ("winrobot-pub-a.oss-cn-hangzhou.aliyuncs.com",
                        "winrobot-pub.oss-cn-hangzhou.aliyuncs.com")
_EXT_CALL_RE = re.compile(r"xbot_extensions\.([0-9a-zA-Z_]+)\.([0-9a-zA-Z_\u4e00-\u9fff]+)")
_SELECTOR_USE_RE = re.compile(r'package\.selector\("([^"]+)"\)')
_FLOW_FILE_RE = re.compile(r"^process(\d+)\.py$")
# .py 里被禁用的扩展积木会退化成整行注释: `# xbot_extensions.<code>.<flow>`
# 注意 [\r] : 影刀导出的流程文件是 CRLF, 行尾的 \r 也要吃掉, 否则 $ 匹配不到
_COMMENT_REF_LINE = re.compile(
    r"^(?P<indent>[ \t]*)#[ \t]*xbot_extensions\.(?P<code>[0-9a-zA-Z_]+)\."
    r"(?P<flow>[0-9a-zA-Z_\u4e00-\u9fff]+)[ \t\r]*$", re.M)
# package_version >= 本值 的非官方指令集会被服务端判为「高级指令集」
_ADVANCED_PACKAGE_VERSION = 8


@dataclass
class InlineReport:
    converted: list[str] = field(default_factory=list)   # "指令集/流程 -> processN"
    dropped_deps: list[str] = field(default_factory=list)  # 从依赖声明里去掉的指令集
    skipped: list[str] = field(default_factory=list)     # 无法无损转换, 原样保留
    kept: list[str] = field(default_factory=list)        # pv<=7 第三方指令集, 无需处理
    warnings: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.converted or self.dropped_deps)

    def summary(self) -> str:
        parts = []
        if self.converted:
            parts.append(f"内联 {len(self.converted)} 个扩展流程")
        if self.dropped_deps:
            parts.append(f"移除 {len(self.dropped_deps)} 个依赖声明")
        if self.skipped:
            parts.append(f"跳过 {len(self.skipped)} 个指令集")
        return "; ".join(parts) or "无需处理"


@dataclass
class _Extension:
    code: str
    name: str
    version: str
    official: bool
    dir: Path
    package: dict
    flow_titles: dict[str, str]
    proto_blocks: dict[str, dict]
    package_version: int = 0

    @property
    def needs_inline(self) -> bool:
        """是否需要内联掉.

        实测结论: 服务端拦的是「非官方 且 指令集包格式版本 >= 8」的指令集
        (报「社区版或基础账号不能使用高级指令集」); pv<=7 的第三方指令集
        (如 飞书发送消息加急指令 pv7) 社区版账号可以正常使用, 官方 pv8 也不受限。
        """
        return not self.official and self.package_version >= _ADVANCED_PACKAGE_VERSION


def _iter_blocks(node):
    """递归遍历 flow.json 里的所有积木节点."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_blocks(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_blocks(value)


def _enabled_refs_from_flow_json(text: str) -> list[tuple[str, str]]:
    """从 .flow.json 里取出「启用状态」的扩展指令引用.

    设计器里被禁用的积木导出到 .py 会变成注释, 但仍留在 flow.json 里并带
    `isEnabled: false`。这类积木不执行, 不能算作"指令集仍在使用" ——
    否则会去内联一个根本没跑的指令集, 内联失败又把依赖留在声明里, 服务端据此拒绝迁移。
    """
    try:
        data = json.loads(text)
    except ValueError:
        return _EXT_CALL_RE.findall(text)
    refs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for block in _iter_blocks(data):
        name = block.get("name")
        if not isinstance(name, str) or name in seen:
            continue
        if block.get("isEnabled") is False:
            continue
        match = _EXT_CALL_RE.fullmatch(name)
        if match:
            seen.add(name)
            refs.append(match.groups())
    return refs


def _selector_ids_by_name(xml: str) -> dict[str, set[str]]:
    """元素库里真正存在的 `<selector name="X" id="...">`.

    判重必须认 `<selector>` 标签本身。元素定位链里常出现 acc-name="下载" /
    uia-name="下载" / x:name="下载" 这类属性, 用 `f'name="{n}" in xml'` 做子串判断
    会把它们当成"元素已存在", 于是整段元素被静默跳过, 迁移后设计器里就找不到它。
    """
    out: dict[str, set[str]] = {}
    for match in re.finditer(r"<selector\b[^>]*>", xml):
        tag = match.group(0)
        name = re.search(r'\bname="([^"]*)"', tag)
        if not name:
            continue
        sid = re.search(r'\bid="([^"]*)"', tag)
        out.setdefault(name.group(1), set()).add(sid.group(1) if sid else "")
    return out


def _selector_id_of(block: str) -> str | None:
    match = re.search(r'<selector\b[^>]*\bid="([^"]*)"', block)
    return match.group(1) if match else None


def _strip_comments(text: str) -> str:
    """去掉 Python 注释, 避免把「注释里残留的调用」当成真实调用.

    影刀导出流程时会把一部分积木结构保留成整行注释, 例如:

        # xbot_extensions.activity_bf7adfac.main

    这些注释并不执行。不剔除就会被误判为"指令集仍在使用", 触发无谓的内联; 内联失败
    后依赖声明又被保留下来, 服务端据此判「社区版或基础账号不能使用高级指令集」.
    """
    try:
        marks = [tok.start for tok in
                 tokenize.generate_tokens(io.StringIO(text).readline)
                 if tok.type == tokenize.COMMENT]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text
    if not marks:
        return text
    lines = text.splitlines()
    for row, col in sorted(marks, reverse=True):
        idx = row - 1
        if 0 <= idx < len(lines):
            lines[idx] = lines[idx][:col]
    return "\n".join(lines)


class CustomActivityInliner:
    def __init__(self, library: BlockLibrary | None = None,
                 extra_custom_codes: set[str] | None = None,
                 force_convert_all_custom: bool = False):
        self.library = library or BlockLibrary.load()
        self.extra_custom_codes = extra_custom_codes or set()
        # 兜底开关: 若哪天服务端规则变了, 打开它就把所有非官方指令集都内联掉
        self.force_convert_all_custom = force_convert_all_custom

    # ------------------------------------------------------------ 入口
    def transform(self, robot_dir: Path, files: dict[str, bytes],
                  package: dict) -> InlineReport:
        """robot_dir: 应用 xbot_robot 目录; files: 将写入包的 {相对路径: 内容}."""
        report = InlineReport()
        extensions = self._scan_extensions(Path(robot_dir).parent / "xbot_extensions")
        if not extensions:
            return report

        used = self._used_flows(files, extensions)
        used = self._expand_internal_calls(used, extensions)
        custom_used = {code: flows for code, flows in used.items()
                       if extensions[code].needs_inline or self.force_convert_all_custom}
        # 第三方但 pv<=7: 服务端允许, 原样保留不折腾
        kept = sorted(code for code in used
                      if not extensions[code].official
                      and not extensions[code].needs_inline)
        unreferenced = [code for code, ext in extensions.items()
                        if not ext.official and code not in used]
        official_used = [code for code in used if extensions[code].official]

        ext_names = {code: ext.name for code, ext in extensions.items()}
        for ext in extensions.values():
            self.library.extend(list(ext.proto_blocks.values()))
        flow_names = {f.get("filename"): f.get("name")
                      for f in package.get("flows") or [] if f.get("filename")}
        taken_names = set(flow_names.values())

        plan: dict[tuple[str, str], tuple[str, str]] = {}
        staged: list[tuple] = []      # 待写入的新流程 (等 plan 建全后再落文件)
        used_indices = self._used_flow_indices(files)
        for code in sorted(custom_used):
            ext_flows = sorted(custom_used[code])
            ext = extensions[code]
            staged_flows: list[tuple[str, str, str]] = []   # (流程名, 显示名, 源码)
            failure = None
            for ext_flow in ext_flows:
                src_file = ext.dir / f"{ext_flow}.py"
                if not src_file.is_file():
                    failure = f"缺少流程文件 {ext_flow}.py"
                    break
                disp = self._flow_display(ext, ext_flow, taken_names)
                source = src_file.read_text(encoding="utf-8")
                try:
                    # 这里只做"能否无损还原"的校验; 真正生成 flow.json 时要用改写后的源码
                    # (否则 flow.json 里的调用目标还是指令集里的旧流程名, 设计器就显示错了)。
                    compiled = FlowCompiler(self.library, disp, flow_names,
                                            ext_names).compile(source)
                except FlowCompileError as exc:
                    failure = f"{disp}: {exc}"
                    break
                if not compiled.lossless:
                    failure = f"{disp}: 无法无损还原 ({'; '.join(compiled.warnings[:2])})"
                    break
                staged_flows.append((ext_flow, disp, source))
            if failure:
                report.skipped.append(f"{ext.name} ({code})")
                report.warnings.append(f"{ext.name}: {failure}; 该指令集保持原样, 需人工处理")
                continue
            # 先只分配文件名, 把 plan 建全 —— 否则写主流程时它调用的子流程还没登记,
            # 像 process="process1" 这样的内部调用就不会被改写, 会指到应用自己的同名流程上。
            for ext_flow, disp, source in staged_flows:
                new_file = f"process{self._take_flow_index(used_indices)}"
                plan[(code, ext_flow)] = (new_file, disp)
                staged.append((ext, ext_flow, source, disp, new_file))

        # plan 已完整, 现在才写流程文件: 先改写 .py 里的调用目标, 再用改写后的源码编译
        # flow.json, 保证设计器里看到的调用目标和实际运行的一致。
        all_flow_names = dict(flow_names)
        all_flow_names.update({new_file: disp for new_file, disp in plan.values()})
        for ext, ext_flow, source, disp, new_file in staged:
            self._write_new_flow(files, package, ext, ext_flow, new_file, disp,
                                 source, plan, all_flow_names, ext_names)
            report.converted.append(f"{ext.name}/{disp} -> {new_file}")

        selector_conflicts: list[str] = []
        if plan:
            self._rewrite_calls(files, plan)
            selector_conflicts = self._merge_selectors(files, extensions, plan)
        drop = sorted({code for code, _ in plan} | set(unreferenced))
        self._drop_dependencies(package, extensions, drop)
        self._neutralize_orphans(files, extensions, drop)   # 见方法注释
        report.dropped_deps = [extensions[c].name for c in drop]
        if unreferenced:
            report.warnings.append(
                "以下指令集未被任何流程引用, 已从依赖声明中移除: "
                + ", ".join(extensions[c].name for c in unreferenced))
        report.kept = [f"{extensions[c].name} ({c}, pv{extensions[c].package_version})"
                       for c in kept]
        if kept:
            report.warnings.append(
                "以下第三方指令集版本(pv<=7)社区版可用, 已原样保留: "
                + ", ".join(f"{extensions[c].name}(pv{extensions[c].package_version})"
                            for c in kept))
        if official_used:
            report.warnings.append(
                "以下为官方市场指令集, 未处理: "
                + ", ".join(extensions[c].name for c in official_used))
        if selector_conflicts:
            report.warnings.append(
                "以下网页元素在应用里已有同名但定义不同的副本, 未合并; "
                "内联后的流程可能指向应用里那个元素, 需人工核对: "
                + ", ".join(selector_conflicts))
        return report

    # ------------------------------------------------------------ 扫描
    def _scan_extensions(self, ext_root: Path) -> dict[str, _Extension]:
        out: dict[str, _Extension] = {}
        if not ext_root.is_dir():
            return out
        for code_dir in sorted(p for p in ext_root.iterdir() if p.is_dir()):
            pkg_file = code_dir / "package.json"
            if not pkg_file.is_file():
                continue
            try:
                pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
            except ValueError:
                continue
            code = pkg.get("activity_code") or code_dir.name
            proto: dict[str, dict] = {}
            proto_file = code_dir / "prototype.block.json"
            if proto_file.is_file():
                try:
                    data = json.loads(proto_file.read_text(encoding="utf-8"))
                    proto = {b["name"]: b for b in data.get("blocks") or []
                             if b.get("name")}
                except (ValueError, KeyError):
                    proto = {}
            out[code] = _Extension(
                code=code, name=pkg.get("name") or code,
                version=str(pkg.get("version") or ""), official=self._is_official(pkg, code),
                package_version=int(pkg.get("package_version") or 0),
                dir=code_dir, package=pkg,
                flow_titles={f.get("filename"): f.get("name")
                             for f in pkg.get("flows") or [] if f.get("filename")},
                proto_blocks=proto)
        return out

    @staticmethod
    def _flow_display(ext: _Extension, ext_flow: str, taken: set[str]) -> str:
        """新流程的显示名: 入口流程沿用扩展块标题, 其它流程加指令集前缀, 并保证唯一."""
        entry_block = ext.proto_blocks.get(f"xbot_extensions.{ext.code}.{ext_flow}")
        title = (entry_block or {}).get("title") or ext.flow_titles.get(ext_flow, ext_flow)
        candidates = [title, f"{ext.name}-{title}", f"{ext.name}-{ext_flow}"]
        for name in candidates:
            if name and name not in taken:
                taken.add(name)
                return name
        name = f"{ext.name}-{ext_flow}"
        i = 2
        while f"{name}({i})" in taken:
            i += 1
        name = f"{name}({i})"
        taken.add(name)
        return name

    def _is_official(self, pkg: dict, code: str) -> bool:
        if code in self.extra_custom_codes:
            return False
        if "official" in str(pkg.get("tags") or ""):
            return True
        icon = str(pkg.get("icon") or "")
        return any(host in icon for host in _OFFICIAL_ICON_HOSTS)

    @staticmethod
    def _used_flow_indices(files: dict[str, bytes]) -> set[int]:
        return {int(m.group(1)) for key in files if (m := _FLOW_FILE_RE.match(key))}

    @staticmethod
    def _take_flow_index(used: set[int]) -> int:
        idx = 1
        while idx in used:
            idx += 1
        used.add(idx)
        return idx

    @staticmethod
    def _expand_internal_calls(used: dict[str, set[str]],
                               extensions: dict[str, _Extension]) -> dict[str, set[str]]:
        """把扩展流程内部互调的其他流程一并纳入 (避免内联后指向应用的同名流程)."""
        out = {code: set(flows) for code, flows in used.items()}
        queue = [(code, f) for code, flows in out.items() for f in flows]
        while queue:
            code, flow = queue.pop()
            src = extensions[code].dir / f"{flow}.py"
            if not src.is_file():
                continue
            for target in re.findall(r'process="([^"]+)"', src.read_text(encoding="utf-8")):
                if target.startswith("xbot_extensions."):
                    code2, flow2 = target.split(".", 2)[1:3]
                    if code2 in extensions and flow2 not in out.get(code2, set()):
                        out.setdefault(code2, set()).add(flow2)
                        queue.append((code2, flow2))
                elif target not in out[code]:
                    out[code].add(target)
                    queue.append((code, target))
        return out

    @staticmethod
    def _used_flows(files: dict[str, bytes],
                    extensions: dict[str, _Extension]) -> dict[str, set[str]]:
        used: dict[str, set[str]] = {}
        for key, data in files.items():
            if not (key.endswith(".py") or key.endswith(".flow.json")):
                continue
            if key.startswith(".dev") and not key.endswith(".flow.json"):
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            # .py 要剔除注释, .flow.json 只看启用状态的积木
            if key.endswith(".flow.json"):
                refs = _enabled_refs_from_flow_json(text)
            else:
                refs = _EXT_CALL_RE.findall(_strip_comments(text))
            for code, flow in refs:
                if code in extensions:
                    used.setdefault(code, set()).add(flow)
        return used

    # ------------------------------------------------------------ 新流程
    def _write_new_flow(self, files: dict[str, bytes], package: dict, ext: _Extension,
                        ext_flow: str, new_file: str, disp: str, source: str,
                        plan: dict, all_flow_names: dict, ext_names: dict) -> None:
        src = source
        old_disp = ext.flow_titles.get(ext_flow, ext_flow)
        if old_disp != disp:
            src = src.replace(f'_block=("{old_disp}"', f'_block=("{disp}"')
        # 指令集内部流程之间的互调, 指向新的应用流程 (含 _block 标题)。
        # 一次扫描替换: 逐个 str.replace 会互相踩 (改出来的新名字可能被后面的规则再改一遍)。
        other = {name: (new_name, disp) for (code, name), (new_name, disp) in plan.items()
                 if code == ext.code and name != ext_flow}

        def _remap(m):
            target = other.get(m.group(1))
            if target is None:
                return m.group(0)
            # 注意补回被模式吃掉的那个右括号, 否则改出来的代码语法就坏了
            return f'process="{target[0]}"' + m.group(2) + f'"{target[1]}")'

        src = re.sub(r'process="([^"]+)"(.*?_block=\("[^"]*",\s*\d+,\s*)"[^"]*"\)',
                     _remap, src, flags=re.S)
        # 没有 _block 的调用 (理论少见) 也要跟着改
        src = re.sub(r'process="([^"]+)"',
                     lambda m: f'process="{other[m.group(1)][0]}"' if m.group(1) in other
                     else m.group(0), src)
        files[f"{new_file}.py"] = src.encode("utf-8")
        # flow.json 必须用**改写后**的源码编译: 否则 design 器里「调用流程」显示的还是指令集里
        # 的旧流程名 (而且用户一保存就会按 flow.json 把 .py 冲回去)。
        compiled = FlowCompiler(self.library, disp, all_flow_names, ext_names).compile(src)
        payload = {"name": disp, "memo": "", "kind": "visual",
                   "blocks": compiled.blocks, "parameters": compiled.parameters}
        files[f".dev/{new_file}.flow.json"] = json.dumps(
            payload, ensure_ascii=False, indent=2).replace("\n", "\r\n").encode("utf-8")
        package.setdefault("flows", []).append(
            {"name": disp, "filename": new_file, "kind": "Visual",
             "opened": False, "groupName": None, "enableCopilot": False})

    # ------------------------------------------------------------ 改写调用
    # ------------------------------------------------------------ 孤儿积木
    def _neutralize_orphans(self, files: dict[str, bytes],
                            extensions: dict[str, _Extension],
                            dropped: list[str]) -> None:
        """把「依赖已删除、流程里却还留着」的禁用积木换成注释块.

        为什么要动: 依赖删掉后目标账号没有这个指令集, 原节点在设计器里渲染不出来,
        会被降级成未知积木 —— 用户看到的效果就是"原本禁用的积木变成启用了", 一保存
        就可能真的去执行它。这些积木本来就是禁用的(`isEnabled: false`, 不执行),
        换成标准注释块既不改变运行行为, 又能在设计器里正常显示、保留原来的标题。
        """
        if not dropped:
            return
        codes = set(dropped)
        for key in list(files):
            if key.endswith(".flow.json"):
                self._neutralize_flow_json(files, key, codes, extensions)
            elif key.endswith(".py"):
                try:
                    text = files[key].decode("utf-8")
                except UnicodeDecodeError:
                    continue
                files[key] = self._neutralize_py(text, codes, extensions).encode("utf-8")

    @staticmethod
    def _neutralize_flow_json(files: dict[str, bytes], key: str, codes: set[str],
                              extensions: dict[str, _Extension]) -> None:
        try:
            data = json.loads(files[key].decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        blocks = data.get("blocks") if isinstance(data, dict) else data
        if not isinstance(blocks, list):
            return
        changed = False
        for index, node in enumerate(blocks):
            if not isinstance(node, dict) or node.get("isEnabled") is not False:
                continue          # 只处理禁用积木; 启用的都走了内联改写
            match = _EXT_CALL_RE.fullmatch(node.get("name") or "")
            if not match or match.group(1) not in codes:
                continue
            code, ext_flow = match.groups()
            ext = extensions.get(code)
            title = node.get("block_title") or (
                f"{ext.name}/{ext_flow}" if ext else ext_flow)
            blocks[index] = {
                "id": node.get("id") or str(uuid.uuid4()),
                "name": "programing.comment",
                "isEnabled": True,
                "inputs": {"content": {
                    "value": f"10:【未迁移，原积木已禁用】{title}"}},
                "outputs": {},
                "isCommentBlockExpanded": True,
            }
            changed = True
        if changed:
            files[key] = json.dumps(data, ensure_ascii=False, indent=2) \
                .replace("\n", "\r\n").encode("utf-8")

    @staticmethod
    def _neutralize_py(text: str, codes: set[str],
                       extensions: dict[str, _Extension]) -> str:
        def repl(match: re.Match) -> str:
            code = match.group("code")
            if code not in codes:
                return match.group(0)
            ext = extensions.get(code)
            title = (f"{ext.name}/{match.group('flow')}" if ext
                     else match.group("flow"))
            # 保留原行尾的 \r, 避免 CRLF 文件里单独这一行变成 LF
            tail = "\r" if match.group(0).endswith("\r") else ""
            return f"{match.group('indent')}# 【未迁移，原积木已禁用】{title}{tail}"

        return _COMMENT_REF_LINE.sub(repl, text)

    def _rewrite_calls(self, files: dict[str, bytes], plan: dict) -> None:
        for key in list(files):
            if key.endswith(".py"):
                files[key] = self._rewrite_py(files[key].decode("utf-8"), plan) \
                    .encode("utf-8")
            elif key.endswith(".flow.json"):
                try:
                    data = json.loads(files[key].decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                if self._rewrite_flow_json(data, plan):
                    files[key] = json.dumps(data, ensure_ascii=False, indent=2) \
                        .replace("\n", "\r\n").encode("utf-8")

    @staticmethod
    def _rewrite_py(text: str, plan: dict) -> str:
        for (code, ext_flow), (new_file, disp) in plan.items():
            pattern = re.compile(
                r'process="xbot_extensions\.' + re.escape(code) + r'\.'
                + re.escape(ext_flow) + r'"(.*?_block=\("[^"]*",\s*\d+,\s*)"[^"]*"\)',
                re.S)
            text = pattern.sub(
                lambda m: f'process="{new_file}"' + m.group(1) + f'"{disp}"', text)
        return text

    @staticmethod
    def _rewrite_flow_json(data: dict, plan: dict) -> bool:
        changed = False
        for block in data.get("blocks") or []:
            name = block.get("name") or ""
            if not name.startswith("xbot_extensions."):
                continue
            target = plan.get(tuple(name.split(".", 2)[1:3]))
            if target is None:
                continue
            new_file, disp = target
            call_args = {k: v.get("value") for k, v in (block.get("inputs") or {}).items()}
            block["name"] = "process.run"
            block.pop("block_title", None)
            block["inputs"] = {
                "process": {"value": f"10:{new_file}", "display": disp},
                "package": {"value": "11:__name__"},
                "inputs": {"value": "16:" + json.dumps(
                    [{k: v} for k, v in call_args.items()],
                    ensure_ascii=False, separators=(",", ":"))},
                "outputs": {"value": "10:[]"},
            }
            block["outputs"] = {"process_result": {"name": "process_result",
                                                  "isEnable": False}}
            changed = True
        return changed

    # ------------------------------------------------------------ 元素库
    def _merge_selectors(self, files: dict[str, bytes],
                         extensions: dict[str, _Extension], plan: dict) -> list[str]:
        """把内联流程用到的元素并进应用元素库.

        返回「同名但定义不同」的元素名: 这种不能直接合并(会指向应用里那个元素),
        也不能静默跳过, 交给调用方报警由人判断。
        """
        xml_key = "selectorsV2.xml"
        if xml_key not in files:
            return []
        needed: dict[str, str] = {}
        resources: set[str] = set()
        for code, ext_flow in sorted(plan):
            ext = extensions[code]
            names = set(_SELECTOR_USE_RE.findall(
                (ext.dir / f"{ext_flow}.py").read_text(encoding="utf-8")))
            sel_file = ext.dir / "selectorsV2.xml"
            if not names or not sel_file.is_file():
                continue
            text = sel_file.read_text(encoding="utf-8")
            for name in names:
                m = re.search(r'<selector[^>]*\bname="' + re.escape(name)
                              + r'"[^>]*(?:/>|>.*?</selector>)', text, re.S)
                if m:
                    needed.setdefault(name, m.group(0))
                    resources |= set(re.findall(r'[a-zA-Z]+="(\.dev\\[^"]+)"', m.group(0)))
        if not needed:
            return []
        app_xml = files[xml_key].decode("utf-8")
        existing = _selector_ids_by_name(app_xml)
        missing: list[str] = []
        conflicts: list[str] = []
        for name, block in needed.items():
            ids = existing.get(name)
            if ids is None:
                missing.append(name)
                continue
            own_id = _selector_id_of(block)
            if own_id and own_id not in ids:
                conflicts.append(name)      # 同名不同元素, 需人工决定
        if not missing:
            return conflicts
        chunk = ('  <group id="%s" name="%s" type="Web" processName="chrome" '
                 'selectorOringin="Inspect">\n' % (uuid.uuid4(), "自定义指令内联元素")) \
            + "\n".join(needed[n] for n in missing) + "\n  </group>\n"
        files[xml_key] = app_xml.replace("</repository>", chunk + "</repository>") \
            .encode("utf-8")
        for res in resources:            # 选择器截图一并搬过来 (路径相对指令集目录)
            rel = res.replace("\\", "/").lstrip("./")
            for code in sorted({c for c, _ in plan}):
                src = extensions[code].dir / rel
                if src.is_file():
                    files[f".dev/{rel}"] = src.read_bytes()
                    break
        return conflicts

    # ------------------------------------------------------------ 依赖声明
    @staticmethod
    def _drop_dependencies(package: dict, extensions: dict[str, _Extension],
                           codes: list[str]) -> None:
        if not codes:
            return
        for field_name in ("internaldependencies", "internalautodependencies"):
            package[field_name] = [
                d for d in package.get(field_name) or []
                if not any(str(d).split("==")[0] == code for code in codes)]
