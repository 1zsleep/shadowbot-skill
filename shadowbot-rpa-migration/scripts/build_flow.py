#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""影刀多块流程组装器 — 从块规格一次生成 .dev/main.flow.json + main.py (不走剪贴板)

用法:
  python build_flow.py --app "<xbot_robot目录>" --spec blocks.json

blocks.json 格式 (数组, 顺序 = 流程顺序):
  [
    {"name": "excel.launch", "overrides": {"launch_way": "10:open", "open_filename": "10:C:\\path\\a.xlsx"}},
    {"name": "excel.get_row_count", "overrides": {"workbook": "11:excel_instance"}},
    {"name": "programing.log", "overrides": {"text": "11:excel_row_count"}},
    {"name": "excel.close", "overrides": {"excel_instance": "11:excel_instance", "close_way": "10:notsave"}}
  ]

值编码同构建器: 10:字符串字面量 / 11:变量引用 / 13:表达式·bool / 16:列表; 裸值自动按参数 type 推断。
块间引用: 用上块输出变量名 (指令定义 outputs 的 name, 如 excel_instance/excel_row_count/excel_data) 做下块 11: 输入。
容器块 (if/for/try) 自动补结束标记, 结束块紧跟开启块, 顺序正确。
已实测: 6 块 Excel 读取流程一次通过。
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    import shadowbot_block_builder as sb
except ImportError:
    # 兄弟技能 shadowbot-block-to-clipboard/scripts/ (同 skills 根下)
    sibling = HERE.parent.parent / 'shadowbot-block-to-clipboard' / 'scripts'
    if sibling.is_dir():
        sys.path.insert(0, str(sibling))
        import shadowbot_block_builder as sb
    else:
        print('找不到 shadowbot_block_builder.py，请确认 shadowbot-block-to-clipboard 技能存在', file=sys.stderr)
        sys.exit(1)


def py_value(v):
    """flow.json 输入值 → main.py python 字面量"""
    if v is None or v.get('value') is None:
        return 'None'
    s = v['value']
    if isinstance(s, str):
        if s.startswith('10:'):
            return json.dumps(s[3:], ensure_ascii=False)
        if s.startswith('11:'):
            return s[3:]
        if s.startswith('13:'):
            return s[3:]
        if s.startswith('16:'):
            return s[3:]
    return repr(s)


def main() -> int:
    ap = argparse.ArgumentParser(description='影刀多块流程组装器')
    ap.add_argument('--app', required=True, help='xbot_robot 应用目录')
    ap.add_argument('--spec', required=True, help='块规格 JSON 文件')
    a = ap.parse_args()

    spec = json.loads(Path(a.spec).read_text(encoding='utf-8'))
    blocks, titles = [], []
    for item in spec:
        b, t = sb.build_block(item['name'], item.get('overrides') or {})
        blocks.extend(b)
        titles.extend(t)

    app = Path(a.app)
    flow = {
        'name': 'main',
        'memo': '我的自动化应用',
        'kind': 'visual',
        'blocks': [{k: v for k, v in b.items() if k != '__kind__'} for b in blocks],
    }
    (app / '.dev').mkdir(parents=True, exist_ok=True)
    (app / '.dev' / 'main.flow.json').write_text(
        json.dumps(flow, ensure_ascii=False, indent=2).replace('\n', '\r\n') + '\r\n',
        encoding='utf-8')

    lines = []
    for i, b in enumerate(blocks, 1):
        args = ', '.join(f'{k}={py_value(v)}' for k, v in b['inputs'].items())
        out_var = next((o['name'] for o in (b.get('outputs') or {}).values() if o.get('isEnable')), None)
        call = f'xbot_visual.{b["name"]}({args}, _block=("main", {i}, {json.dumps(titles[i - 1], ensure_ascii=False)}))'
        lines.append(f'        {out_var} = {call}' if out_var else f'        {call}')

    py = (
        'import xbot\n'
        'import xbot_visual\n'
        'from . import package\n'
        'from .package import variables as glv\n'
        'import time\n'
        'from xbot import print\n'
        '\n'
        'def main(args):\n'
        '    try:\n'
        + '\n'.join(lines)
        + '\n    finally:\n'
        '        pass\n'
    )
    (app / 'main.py').write_text(py, encoding='utf-8')

    print(f'✅ 已写入 {(app / ".dev" / "main.flow.json")} ({len(flow["blocks"])} 块) + main.py')
    print('titles:', titles)
    return 0


if __name__ == '__main__':
    sys.exit(main())
