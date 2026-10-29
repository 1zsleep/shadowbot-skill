#!/usr/bin/env python3
"""
.NET Reactor 反混淆 - PE 重建工具 (通用)
用法: python reconstruct.py <原DLL路径> <manifest目录> <输出DLL路径>

manifest 目录需含 manifest.tsv (由 sbdump 生成) 和 il/ 子目录。
"""
import struct, os, sys

def align_up(x, a):
    return (x + a - 1) & ~(a - 1)

def main():
    if len(sys.argv) < 4:
        print("usage: reconstruct.py <dll> <manifest_dir> <out.dll>")
        return 1
    DLL, MANDIR, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
    MANIFEST = os.path.join(MANDIR, "manifest.tsv")
    ILDIR = os.path.join(MANDIR, "il")

    data = bytearray(open(DLL, "rb").read())
    orig_len = len(data)

    pe = struct.unpack_from("<I", data, 0x3C)[0]
    num_sections = struct.unpack_from("<H", data, pe + 6)[0]
    opt_size = struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    assert magic == 0x20b, "PE32+ expected"

    sec_align = struct.unpack_from("<I", data, opt + 32)[0]
    file_align = struct.unpack_from("<I", data, opt + 36)[0]
    size_of_image = struct.unpack_from("<I", data, opt + 56)[0]
    size_of_headers = struct.unpack_from("<I", data, opt + 60)[0]

    ddir = opt + 112
    cli_rva, cli_size = struct.unpack_from("<II", data, ddir + 14 * 8)

    # ---- sections ----
    sec = opt + opt_size
    sections = []
    for i in range(num_sections):
        vsize, vaddr, rsize, raddr = struct.unpack_from("<IIII", data, sec + i * 40 + 8)
        sections.append((vaddr, vsize, raddr, rsize))

    def rva_to_off(rva):
        for (vaddr, vsize, raddr, rsize) in sections:
            if vaddr <= rva < vaddr + max(vsize, rsize):
                return raddr + (rva - vaddr)
        return None

    cli_off = rva_to_off(cli_rva)
    meta_rva, meta_size = struct.unpack_from("<II", data, cli_off + 8)
    meta_off = rva_to_off(meta_rva)

    assert data[meta_off:meta_off + 4] == b"BSJB"
    ver_len = struct.unpack_from("<I", data, meta_off + 12)[0]
    ver_str_off = meta_off + 16
    ver_str_len = align_up(ver_len, 4)
    streams_hdr_off = ver_str_off + ver_str_len
    _, nstreams = struct.unpack_from("<HH", data, streams_hdr_off)
    streams = []
    off = streams_hdr_off + 4
    for _ in range(nstreams):
        so, ss = struct.unpack_from("<II", data, off)
        name = data[off + 8:].split(b"\0", 1)[0].decode()
        streams.append((name, meta_off + so, ss))
        off += 8 + align_up(len(name) + 1, 4)

    tables_off, _ = next((o, s) for n, o, s in streams if n == "#~")
    t = tables_off + 4
    heap_sizes = data[t + 2]
    t += 4
    valid = struct.unpack_from("<Q", data, t)[0]
    t += 8
    t += 8  # sorted
    present = [i for i in range(64) if (valid >> i) & 1]
    row_counts = {}
    for i in present:
        row_counts[i] = struct.unpack_from("<I", data, t)[0]
        t += 4

    str4 = (heap_sizes & 0x01) != 0
    guid4 = (heap_sizes & 0x02) != 0
    blob4 = (heap_sizes & 0x04) != 0

    def idx_size_for_table(tbl):
        return 2 if row_counts.get(tbl, 0) <= 0xFFFF else 4

    def table_row_size(i):
        s, b, g = str4, blob4, guid4
        layouts = {
            0: [("c",2),("s",),("g",),("g",),("g",)],
            1: [("t",0),("s",),("s",)],
            2: [("c",4),("s",),("s",),("t",0),("t",4),("t",6)],
            3: [("t",4)],
            4: [("c",2),("s",),("b",)],
            5: [("t",6)],
            6: [("c",4),("c",2),("c",2),("s",),("b",),("t",8)],
            7: [("t",8)],
            8: [("c",2),("c",2),("s",)],
        }
        if i not in layouts:
            return 0
        total = 0
        for col in layouts[i]:
            k = col[0]
            if k == "c": total += col[1]
            elif k == "s": total += 4 if s else 2
            elif k == "b": total += 4 if b else 2
            elif k == "g": total += 4 if g else 2
            elif k == "t": total += idx_size_for_table(col[1])
        return total

    table_data_start = t
    methoddef_data_off = table_data_start
    for i in present:
        if i == 6: break
        methoddef_data_off += row_counts[i] * table_row_size(i)
    methoddef_row_size = table_row_size(6)
    methoddef_rows = row_counts.get(6, 0)

    print(f"MethodDef rows={methoddef_rows} row_size={methoddef_row_size}")

    # ---- read manifest ----
    methods = []
    for line in open(MANIFEST):
        p = line.rstrip("\n").split("\t")
        if len(p) < 6: continue
        methods.append((int(p[0], 16), int(p[1]), int(p[2]), int(p[3]), p[5]))

    def make_fat(il):
        hdr = struct.pack("<HHII", 0x3003, 8, len(il), 0)
        return hdr + il

    new_bodies = {}
    cur = 0
    for tok, rva, hs, ilsz, fname in methods:
        with open(os.path.join(ILDIR, fname), "rb") as f:
            il = f.read()
        body = make_fat(il)
        new_bodies[tok] = (cur, body)
        cur += align_up(len(body), 4)
    total_new = cur
    print(f"total new body bytes: {total_new:#x}")

    # ---- add new section ----
    last_va = max(vaddr + vsize for (vaddr, vsize, raddr, rsize) in sections)
    new_va = align_up(last_va, sec_align)
    new_raw = align_up(orig_len, file_align)
    new_vsize = total_new
    new_rsize = align_up(total_new, file_align)

    new_sec_hdr_off = sec + num_sections * 40
    first_section_raw = min(raddr for (_, _, raddr, _) in sections)
    assert new_sec_hdr_off + 40 <= first_section_raw, "no room for extra section header"

    name = b".sbnew".ljust(8, b"\0")
    hdr = name + struct.pack("<IIIIIIHHI", new_vsize, new_va, new_rsize, new_raw, 0, 0, 0, 0, 0x40000040)
    data[new_sec_hdr_off:new_sec_hdr_off + 40] = hdr

    data.extend(b"\0" * (new_raw + new_rsize - len(data)))
    for tok, (offb, body) in new_bodies.items():
        data[new_raw + offb:new_raw + offb + len(body)] = body

    struct.pack_into("<H", data, pe + 6, num_sections + 1)
    struct.pack_into("<I", data, opt + 56, max(size_of_image, align_up(new_va + new_vsize, sec_align)))

    patched = 0
    for tok, rva, hs, ilsz, fname in methods:
        if tok >> 24 != 0x06: continue
        row = (tok & 0xFFFFFF) - 1
        if row >= methoddef_rows: continue
        row_off = methoddef_data_off + row * methoddef_row_size
        struct.pack_into("<I", data, row_off, new_va + new_bodies[tok][0])
        patched += 1

    print(f"patched {patched} MethodDef RVAs")
    os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
    open(OUT, "wb").write(bytes(data))
    print("written -> " + OUT)
    return 0

if __name__ == "__main__":
    sys.exit(main())
