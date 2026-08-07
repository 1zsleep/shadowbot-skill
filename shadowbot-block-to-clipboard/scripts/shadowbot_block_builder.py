# -*- coding: utf-8 -*-
"""影刀指令构建器 —— 按指令库定义生成任意可视化块, 写入剪贴板 (影刀 Ctrl+V 还原)。

指令库来源: ShadowBot.Runtime.Development.dll 内嵌资源 (已提取到 shadowbot_blocklib/),
覆盖 425 个内置指令 (web/excel/word/email/ocr/database/workflow/dialog/process...)。

用法:
  python shadowbot_block_builder.py <指令名> [--set 参数=值 ...]
  python shadowbot_block_builder.py --search <关键词>
  python shadowbot_block_builder.py --list [分类关键词]

示例:
  # IF 条件: 如果 dialog_result.pressed_button == "取消"
  python shadowbot_block_builder.py workflow.if --set operand1=11:dialog_result.pressed_button --set operator=10:== --set operand2=10:取消
  # 增加时间 2 天 (自动带 display=增加/天, 自动输出 datetime_instance)
  python shadowbot_block_builder.py datetime.add --set duration=10:2 --set unit=10:day
  # 打印日志
  python shadowbot_block_builder.py programing.log --set text=10:hello

值写法: 10:=字符串字面量, 11:=变量引用, 13:=Python表达式/bool, 16:=列表
(不带前缀时按参数类型自动加: bool->13:, 其他->10:; select 参数自动补 display)
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile

LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'shadowbot_blocklib')
FLOW_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'shadowbot_flow_clipboard.py')
# 扩展指令定义: 自动探测本机影刀用户目录 (%LOCALAPPDATA%\ShadowBot\users), 可用环境变量 SB_USERS_DIR 覆盖
_USERS_DIR = os.environ.get('SB_USERS_DIR') or os.path.expandvars(r'%LOCALAPPDATA%\ShadowBot\users')
EXT_GLOB = os.path.join(_USERS_DIR, '*', 'apps', '*', 'xbot_extensions', '*', 'prototype.block.json')

_catalog = None


def catalog():
    """标准指令(425) + 扩展自定义指令(机器上 59 个扩展 ~4585 个)"""
    global _catalog
    if _catalog is None:
        _catalog = {}
        for f in glob.glob(os.path.join(LIB_DIR, 'Zh_CN_*_blocks_json')):
            try:
                d = json.load(open(f, encoding='utf-8'))
            except Exception:
                continue
            for b in d.get('blocks', []):
                if b.get('name'):
                    _catalog[b['name']] = b
        # 扩展自定义指令: 每个 prototype.block.json 一个扩展
        for f in glob.glob(EXT_GLOB):
            try:
                d = json.load(open(f, encoding='utf-8'))
            except Exception:
                continue
            for b in d.get('blocks', []):
                if b.get('name') and b['name'] not in _catalog:
                    _catalog[b['name']] = b
    return _catalog


def typed_value(def_input, raw):
    """raw -> {value, display?}"""
    if raw is None:
        return {'value': None}
    s = str(raw)
    if s.startswith(('10:', '11:', '13:', '16:')):
        value = s
    elif def_input.get('type') == 'bool':
        value = '13:' + s
    else:
        value = '10:' + s
    out = {'value': value}
    if value.startswith('11:'):
        out['display'] = value[3:]  # 变量引用: display=变量名 (影刀 UI 行为)
        return out
    # select 参数: 从 options 找 display (忽略 10:/11: 前缀)
    editor = def_input.get('editor') or {}
    if editor.get('kind') == 'select':
        bare = s[3:] if s.startswith(('10:', '11:', '13:', '16:')) else s
        for opt in editor.get('options', []):
            if opt.get('value') == bare or opt.get('display') == bare:
                out['display'] = opt['display']
                break
    return out


def end_block_for(name, d):
    """容器开启块 -> 结束标记块名 (scope=1 的 if/循环/异常), 中间块(elseif/catch等)返回 None"""
    if name == 'programing.try':
        return 'programing.endtry'
    if d.get('indent') in ('1', 1) or d.get('scope') in ('1', 1):
        if d.get('isCondition'):
            return 'workflow.endif'
        if d.get('isLoop'):
            return 'workflow.endloop'
    return None


def build_block(name, overrides, with_end=True):
    cat = catalog()
    if name not in cat:
        sys.exit('找不到指令 %r\n用 --search 关键词 查找' % name)
    d = cat[name]
    inputs = {}
    for i in (d.get('inputs') or []):
        iname = i['name']
        if iname in overrides:
            inputs[iname] = typed_value(i, overrides[iname])
        else:
            default = i.get('default')
            if default is None or default == 'None':
                if i.get('required'):
                    opts = (i.get('editor') or {}).get('options') or []
                    if opts:  # 必填且无默认: select 取第一个选项 (如 driver_way->auto_check)
                        first = opts[0]
                        inputs[iname] = {'value': '10:' + first['value'], 'display': first['display']}
                    else:
                        inputs[iname] = {'value': None}
                        print('警告: %s.%s 必填且无默认值, 需 --set %s=... 指定' % (name, iname, iname), file=sys.stderr)
                else:
                    inputs[iname] = {'value': None}
            else:
                v = {'value': default}
                if i.get('defaultDisplay'):
                    v['display'] = i['defaultDisplay']
                inputs[iname] = v
    outputs = {}
    for o in (d.get('outputs') or []):
        key = o.get('id') or o.get('name')  # 输出键 = 定义里的 id (可能≠name!)
        val = {'name': o.get('name') or key, 'isEnable': True}
        if o.get('variableLabel'):
            val['variableLabel'] = o['variableLabel']
        if o.get('type'):
            val['type'] = o['type']
        outputs[key] = val
    block = {
        'id': __import__('uuid').uuid4().__str__(),
        'name': name,
        'isEnabled': True,
    }
    if d.get('comment'):
        block['comment'] = d['comment']
    if d.get('isCondition') or d.get('isLoop'):
        block['foldState'] = 'UnFold'
    if name.startswith('xbot_extensions.'):
        block['block_title'] = (d.get('extension') or '') + '/' + d.get('title', '')
    block['inputs'] = inputs
    block['outputs'] = outputs
    block['__kind__'] = 0

    blocks, titles = [block], [d.get('title', name)]
    # 容器块自动补结束标记: if->endif, 循环->endloop, try->catch+endtry
    if with_end:
        if name == 'programing.try':
            extra = ['programing.catch', 'programing.endtry']
        else:
            end_name = end_block_for(name, d)
            extra = [end_name] if end_name else []
        for en in extra:
            eb, et = build_block(en, {}, with_end=False)
            blocks.extend(eb)
            titles.extend(et)
    if name == 'excel.launch':
        print('提示: 打开类指令记得在流程末尾配 excel.close (关闭Excel)', file=sys.stderr)
    return blocks, titles


def to_clipboard(blocks, title):
    tmp = tempfile.NamedTemporaryFile('w', encoding='utf-8', suffix='.json', delete=False)
    json.dump(blocks, tmp, ensure_ascii=False)
    tmp.close()
    try:
        r = subprocess.run([sys.executable, FLOW_SCRIPT, tmp.name, '--titles', title],
                           capture_output=True, text=True, encoding='utf-8', errors='replace')
        print(r.stdout.strip())
        if r.returncode != 0:
            print(r.stderr, file=sys.stderr)
            sys.exit(1)
    finally:
        os.unlink(tmp.name)


def main():
    ap = argparse.ArgumentParser(description='影刀指令构建器 (写入剪贴板, 影刀内 Ctrl+V 还原)')
    ap.add_argument('name', nargs='?', help='指令名, 如 workflow.if / datetime.add')
    ap.add_argument('--set', action='append', default=[], metavar='参数=值',
                    help='设置输入参数, 可多次; 值带 10:/11:/13:/16: 前缀或裸值(自动按类型)')
    ap.add_argument('--no-end', action='store_true',
                    help='容器块不自动补结束标记 (if 不加 endif, 循环不加 endloop, try 不加 catch/endtry)')
    ap.add_argument('--info', metavar='指令名',
                    help='查看指令的输入/输出定义 (不写剪贴板)')
    ap.add_argument('--search', help='按关键词搜索指令 (name/title/description)')
    ap.add_argument('--list', action='store_true', help='列出全部指令')
    a = ap.parse_args()

    cat = catalog()
    if a.list:
        kw = (a.name or '').lower()
        for n, d in sorted(cat.items()):
            if kw in n.lower() or kw in (d.get('title') or '').lower():
                print('%-38s %s' % (n, d.get('title', '')))
        return
    if a.search:
        kw = a.search.lower()
        hits = 0
        for n, d in sorted(cat.items()):
            hay = (n + ' ' + (d.get('title') or '') + ' ' + (d.get('description') or '')).lower()
            if kw in hay:
                print('%-38s %s' % (n, d.get('title', '')))
                hits += 1
        print('共 %d 条' % hits)
        return
    if a.info:
        if a.info not in cat:
            sys.exit('找不到指令 %r\n用 --search 关键词 查找' % a.info)
        d = cat[a.info]
        print('=== %s (%s) ===' % (d.get('title', a.info), a.info))
        if d.get('comment'):
            print('模板: %s' % d['comment'])
        ins = d.get('inputs') or []
        print('\n输入 (%d 个):' % len(ins))
        for i in ins:
            opts = ''
            editor = i.get('editor') or {}
            if editor.get('kind') == 'select':
                opts = '  选项: ' + ' | '.join('%s=%s' % (o.get('display'), o.get('value'))
                                               for o in editor.get('options', []))
            print('  - %-22s %-28s 默认: %s%s' % (
                i.get('name'), '(%s)' % (i.get('type') or 'any'),
                i.get('default') if i.get('default') is not None else 'None', opts))
        outs = d.get('outputs') or []
        print('\n输出 (%d 个):' % len(outs))
        for o in outs:
            print('  - %-22s %s' % (o.get('name'), '(%s)' % (o.get('type') or 'any')))
        print()
        return
    if not a.name:
        ap.print_help()
        return

    overrides = {}
    for kv in a.set:
        if '=' not in kv:
            sys.exit('--set 格式: 参数=值 (如 --set duration=10:2)')
        k, v = kv.split('=', 1)
        overrides[k.strip()] = v

    blocks, titles = build_block(a.name, overrides, with_end=not a.no_end)
    for b in blocks:
        print(json.dumps(b, ensure_ascii=False, indent=1))
    to_clipboard(blocks, '|'.join(titles))
    print('已写入剪贴板: %s (%s)' % (' + '.join(titles), a.name))


if __name__ == '__main__':
    main()
