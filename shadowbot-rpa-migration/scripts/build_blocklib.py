"""从影刀指令库 (shadowbot-block-to-clipboard 技能自带) 抽出精简指令定义, 生成随程序打包的资源.

用法:
    python scripts/build_blocklib.py [指令库目录] [输出文件]

指令库目录的查找顺序 (优先级从高到低):
    1. 命令行第一个参数
    2. 环境变量 SHADOWBOT_BLOCKLIB_DIR
    3. 兄弟技能目录 —— 从本技能目录往上逐级查找
       <某一层>/shadowbot-block-to-clipboard/scripts/shadowbot_blocklib
    4. 同级任意技能的指令库: <某一层>/<任意技能>/scripts/shadowbot_blocklib
    5. 本技能目录内自带的 vendor/shadowbot_blocklib (单独拷走本技能时用)

默认输出: <本技能目录>/src/migration_assistant/resources/shadowbot_blocklib.json

只保留编译器需要的字段: 块的 name / function / inputs(名称,类型,默认值,下拉选项) / outputs(id,变量名,类型)。
影刀升级后重新运行本脚本刷新即可。
"""
from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

_SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUT = _SKILL_DIR / "src" / "migration_assistant" / "resources" / "shadowbot_blocklib.json"

_LIB_LEAF = Path("scripts") / "shadowbot_blocklib"
_LIB_SKILL = "shadowbot-block-to-clipboard"
_MAX_UP = 5          # 从本技能目录往上找几层 (仓库根 → 分组目录 → …)


def _candidate_lib_dirs() -> list[Path]:
    """按可用性排序的指令库候选目录 (全部位于本仓库内, 不引用宿主安装目录)."""
    cands: list[Path] = []
    env = os.environ.get("SHADOWBOT_BLOCKLIB_DIR")
    if env:
        cands.append(Path(env).expanduser())

    cur = _SKILL_DIR
    for _ in range(_MAX_UP):
        cands.append(cur / _LIB_SKILL / _LIB_LEAF)
        # 同层其它技能目录里带同款指令库也认 (防止目录改名)
        cands.extend(sorted(cur.glob(f"*/{_LIB_LEAF.as_posix()}")))
        if cur.parent == cur:
            break
        cur = cur.parent

    # 单技能自带副本: 只拷 shadowbot-rpa-migration 到别的项目时用
    cands.append(_SKILL_DIR / "vendor" / "shadowbot_blocklib")

    seen: set[str] = set()
    uniq: list[Path] = []
    for c in cands:
        key = str(c).lower()
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    return uniq


def resolve_lib_dir(explicit: Path | None = None) -> Path | None:
    if explicit is not None:
        return explicit if explicit.is_dir() else None
    cands = _candidate_lib_dirs()
    for c in cands:
        if c.is_dir():
            return c
    return None


def _iter_blocks(node):
    if isinstance(node, list):
        for item in node:
            yield from _iter_blocks(item)
    elif isinstance(node, dict):
        if "name" in node and ("inputs" in node or "outputs" in node or "function" in node):
            yield node


def _compact_input(inp: dict) -> dict:
    out = {"name": inp.get("name"), "type": inp.get("type")}
    if inp.get("default") is not None:
        out["default"] = inp["default"]
    if inp.get("required"):
        out["required"] = True
    options = ((inp.get("editor") or {}).get("options")) or inp.get("options")
    if options:
        out["options"] = [
            {"value": o.get("value"), "display": o.get("display")}
            for o in options if isinstance(o, dict) and o.get("value") is not None
        ]
    return out


def _compact_output(out: dict) -> dict:
    return {"id": out.get("id") or out.get("name"),
            "name": out.get("name"), "type": out.get("type")}


def build(lib_dir: Path) -> dict:
    by_block: dict[str, dict] = {}
    by_function: dict[str, dict] = {}
    for path in sorted(glob.glob(str(lib_dir / "*"))):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        blocks = data.get("blocks") if isinstance(data, dict) else data
        for blk in _iter_blocks(blocks):
            name = blk.get("name")
            if not name or name in by_block:
                continue
            entry = {
                "name": name,
                "function": blk.get("function"),
                "inputs": [_compact_input(i) for i in (blk.get("inputs") or [])
                           if isinstance(i, dict) and i.get("name")],
                "outputs": [_compact_output(o) for o in (blk.get("outputs") or [])
                            if isinstance(o, dict)],
                "isCondition": bool(blk.get("isCondition")),
                "isLoop": bool(blk.get("isLoop")),
            }
            by_block[name] = entry
            fn = blk.get("function")
            if fn and fn not in by_function:
                by_function[fn] = name
    return {"byBlock": by_block, "functionToBlock": by_function}


def main() -> int:
    explicit = None
    if len(sys.argv) > 1 and sys.argv[1].strip():
        explicit = Path(sys.argv[1]).expanduser()
    out_path = (Path(sys.argv[2]).expanduser() if len(sys.argv) > 2 and sys.argv[2].strip()
                else DEFAULT_OUT)
    lib_dir = resolve_lib_dir(explicit)
    if lib_dir is None:
        print("找不到指令库目录。请显式传入, 或设置环境变量 SHADOWBOT_BLOCKLIB_DIR。",
              file=sys.stderr)
        for c in ([explicit] if explicit else []) + _candidate_lib_dirs():
            print(f"  候选: {c}  (存在={c.is_dir()})", file=sys.stderr)
        return 2
    print(f"指令库目录: {lib_dir}")
    lib = build(lib_dir)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(lib, ensure_ascii=False, separators=(",", ":")),
                        encoding="utf-8")
    size_kb = out_path.stat().st_size / 1024
    print(f"块定义 {len(lib['byBlock'])} 个, 函数映射 {len(lib['functionToBlock'])} 条")
    print(f"写入 {out_path} ({size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
