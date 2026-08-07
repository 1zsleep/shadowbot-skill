# -*- coding: utf-8 -*-
"""把影刀(RPA)可视化流程块写入剪贴板 —— 在影刀编辑器里 Ctrl+V 即可还原为可视化指令。

原理（逆向自剪贴板分析）:
  影刀复制块时剪贴板含 5 种格式，识别关键 = 自定义格式 ShadowBot.Flow.Blocks：
    .NET DataObject.SetData("ShadowBot.Flow.Blocks", json字符串)
    -> BinaryFormatter 序列化 (37字节头 + UTF-8 JSON)
  另附 System.String / UnicodeText / Text (块标题) + HTML Format
    (<shadowbot id="blocks"> 内嵌 {"version":"1.0.1","contentType":1,"data":"<base64>"})
  本脚本用同款 .NET API（经 powershell.exe）写入，字节级一致。

用法:
  python shadowbot_flow_clipboard.py <flow.json 或 blocks数组.json> [--titles "标题1|标题2"] [--new-ids]
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import uuid

PS_SET = r"""
param([string]$dir)
Add-Type -AssemblyName System.Windows.Forms
$blocks = Get-Content (Join-Path $dir 'blocks.txt') -Raw -Encoding UTF8
$html   = Get-Content (Join-Path $dir 'html.txt')   -Raw -Encoding UTF8
$titles = Get-Content (Join-Path $dir 'titles.txt') -Raw -Encoding UTF8
$do = New-Object System.Windows.Forms.DataObject
$do.SetData("ShadowBot.Flow.Blocks", $blocks)
$do.SetData("System.String", $titles)
$do.SetData([System.Windows.Forms.DataFormats]::UnicodeText, $titles)
$do.SetData([System.Windows.Forms.DataFormats]::Text, $titles)
$do.SetData("HTML Format", $html)
[System.Windows.Forms.Clipboard]::SetDataObject($do, $true)
Write-Output 'clipboard set OK'
"""


def load_blocks(path):
    with open(path, encoding='utf-8') as f:
        obj = json.load(f)
    if isinstance(obj, dict) and isinstance(obj.get('blocks'), list):
        return obj['blocks']
    if isinstance(obj, list):
        return obj
    raise ValueError('文件里没有 blocks 数组')


def main():
    ap = argparse.ArgumentParser(description='影刀流程块 -> 剪贴板 (粘贴进影刀还原可视化)')
    ap.add_argument('flow', help='flow.json 或 blocks 数组 json 文件')
    ap.add_argument('--titles', default='',
                    help='各块显示名, 用 | 分隔; 缺省取块 name 字段')
    ap.add_argument('--new-ids', action='store_true',
                    help='为每个块生成新 uuid (粘贴进已有流程时防 id 冲突)')
    a = ap.parse_args()

    blocks = load_blocks(a.flow)
    if not blocks:
        sys.exit('blocks 为空')
    if a.new_ids:
        for b in blocks:
            b['id'] = str(uuid.uuid4())
    for b in blocks:
        b.setdefault('__kind__', 0)

    titles = [t for t in a.titles.split('|') if t] or [b.get('name', '') for b in blocks]

    blocks_json = json.dumps(blocks, ensure_ascii=False, separators=(',', ':'))
    b64 = base64.b64encode(blocks_json.encode('utf-8')).decode('ascii')
    wrapper = '{"version":"1.0.1","contentType":1,"data":"%s"}' % b64
    html = ('Version:1.0\r\nStartHTML:0000000105\r\nEndHTML:0000000233\r\n'
            'StartFragment:0000000121\r\nEndFragment:0000000215\r\n'
            '<html>\r\n<body>\r\n<!--StartFragment-->'
            '<shadowbot id="blocks" style="display:none">' + wrapper +
            '</shadowbot><!--EndFragment-->\r\n</body>\r\n</html>')
    titles_text = '\r\n'.join(titles)

    tmp = tempfile.mkdtemp(prefix='sb_clip_')
    try:
        with open(os.path.join(tmp, 'blocks.txt'), 'w', encoding='utf-8', newline='') as f:
            f.write(blocks_json)
        with open(os.path.join(tmp, 'html.txt'), 'w', encoding='utf-8', newline='') as f:
            f.write(html)
        with open(os.path.join(tmp, 'titles.txt'), 'w', encoding='utf-8', newline='') as f:
            f.write(titles_text)
        ps = os.path.join(tmp, 'set.ps1')
        with open(ps, 'w', encoding='utf-8-sig') as f:
            f.write(PS_SET)
        r = subprocess.run(
            ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ps, '-dir', tmp],
            capture_output=True, text=True, encoding='utf-8', errors='replace')
        print(r.stdout.strip())
        if r.returncode != 0:
            print(r.stderr, file=sys.stderr)
            sys.exit(1)
        print('已写入剪贴板, %d 个块' % len(blocks))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
