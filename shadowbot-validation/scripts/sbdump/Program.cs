using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Reflection.Metadata;
using System.Reflection.Metadata.Ecma335;
using System.Reflection.PortableExecutable;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Text;

// sbdump —— .NET Reactor 保护 DLL 的运行时 IL / 字符串抽取工具
//
// 原理：Reactor 把方法体抽空成 stub，真实 IL 在模块初始化时解密后
// 存进一个 Hashtable（key = IL 在进程内的绝对地址，value = {size, dec, orig}）。
// 只要让 assembly 在进程里真正加载一次，就能从这张表里把 IL 全部取回来。
//
// 用法：
//   sbdump dump <target.dll> <out-dir>          导出全部解密 IL + manifest.tsv
//   sbdump dump <target.dll> <out-dir> <filter>  只导出类型名包含 <filter> 的方法
//   sbdump str  <target.dll> <hex-offset> [...]  解出字符串常量表的指定偏移
//   sbdump list <target.dll>                     列出解出的方法数量与前若干条
class Program
{
    static string DllDir;

    static int Main(string[] args)
    {
        if (args.Length < 2)
        {
            Console.Error.WriteLine("usage: sbdump <dump|str|list> <dll> [args...]");
            return 2;
        }
        string mode = args[0];
        string dll = Path.GetFullPath(args[1]);
        if (!File.Exists(dll))
        {
            Console.Error.WriteLine("not found: " + dll);
            return 2;
        }
        DllDir = Path.GetDirectoryName(dll);

        // 依赖 DLL 与目标同目录，直接按名字加载
        AppDomain.CurrentDomain.AssemblyResolve += (s, e) =>
        {
            string n = new AssemblyName(e.Name).Name;
            string p = Path.Combine(DllDir, n + ".dll");
            return File.Exists(p) ? Assembly.LoadFrom(p) : null;
        };

        Assembly asm;
        try
        {
            asm = Assembly.LoadFrom(dll);
            RuntimeHelpers.RunModuleConstructor(asm.ManifestModule.ModuleHandle);
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine("load failed: " + ex.GetType().Name + ": " + ex.Message);
            Console.Error.WriteLine("hint: 确保影刀/DLL 的全部依赖都在同一目录");
            return 3;
        }

        switch (mode)
        {
            case "dump":
            {
                string outDir = args.Length > 2 ? args[2] : Path.Combine(DllDir, "sbdump_out");
                string filter = args.Length > 3 ? args[3] : null;
                return Dump(asm, dll, outDir, filter);
            }
            case "list":
                return List(asm);
            case "str":
                return Strings(asm, args);
            default:
                Console.Error.WriteLine("unknown mode: " + mode);
                return 2;
        }
    }

    // ---------------- 定位解密表 ----------------

    sealed class Entry { public int Size; public IntPtr Dec; }

    static Hashtable FindTable(Assembly asm)
    {
        Hashtable best = null;
        foreach (var t in SafeTypes(asm))
        {
            FieldInfo[] fs;
            try { fs = t.GetFields(BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic); }
            catch { continue; }
            foreach (var f in fs)
            {
                if (f.FieldType != typeof(Hashtable)) continue;
                Hashtable h;
                try { h = (Hashtable)f.GetValue(null); } catch { continue; }
                if (h == null) continue;
                if (best == null || h.Count > best.Count) best = h;
            }
        }
        return best;
    }

    static IEnumerable<Type> SafeTypes(Assembly asm)
    {
        Type[] ts;
        try { ts = asm.GetTypes(); }
        catch (ReflectionTypeLoadException ex) { ts = ex.Types; }
        foreach (var t in ts) if (t != null) yield return t;
    }

    // 表里 value 是 {int size; IntPtr dec; IntPtr orig} 的 boxed struct，
    // 字段名每次混淆不同，按 metadata 声明顺序反射读取。
    static Entry ReadEntry(object v)
    {
        var t = v.GetType();
        var fs = t.GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
        if (fs.Length < 2) return null;
        var e = new Entry();
        bool gotSize = false, gotDec = false;
        foreach (var f in fs)
        {
            object val = f.GetValue(v);
            if (!gotSize && f.FieldType == typeof(int)) { e.Size = (int)val; gotSize = true; continue; }
            if (!gotDec && f.FieldType == typeof(IntPtr)) { e.Dec = (IntPtr)val; gotDec = true; continue; }
        }
        return (gotSize && gotDec) ? e : null;
    }

    static long ModuleBase(string dllPath)
    {
        string name = Path.GetFileName(dllPath);
        foreach (ProcessModule m in Process.GetCurrentProcess().Modules)
        {
            try { if (string.Equals(m.ModuleName, name, StringComparison.OrdinalIgnoreCase)) return m.BaseAddress.ToInt64(); }
            catch { }
        }
        return 0;
    }

    // ---------------- dump ----------------

    static int Dump(Assembly asm, string dllPath, string outDir, string filter)
    {
        var tbl = FindTable(asm);
        if (tbl == null)
        {
            Console.Error.WriteLine("no decryption table found — 该 DLL 可能未被 .NET Reactor 保护，或模块初始化未触发");
            return 4;
        }
        Console.WriteLine("table entries: " + tbl.Count);

        long baseAddr = ModuleBase(dllPath);
        if (baseAddr == 0)
        {
            Console.Error.WriteLine("cannot resolve module base address");
            return 4;
        }
        Console.WriteLine($"module base: 0x{baseAddr:X}");

        string ilDir = Path.Combine(outDir, "il");
        Directory.CreateDirectory(ilDir);
        var manifest = new StringBuilder();
        int hit = 0, miss = 0;

        var bytes = File.ReadAllBytes(dllPath);
        using var peReader = new PEReader(new MemoryStream(bytes));
        var md = peReader.GetMetadataReader();

        foreach (var handle in md.MethodDefinitions)
        {
            var mdef = md.GetMethodDefinition(handle);
            int rva = mdef.RelativeVirtualAddress;
            if (rva == 0) continue;

            int token = 0x06000000 | MetadataTokens.GetRowNumber(handle);
            string typeName = "";
            try
            {
                var tdHandle = mdef.GetDeclaringType();
                if (!tdHandle.IsNil)
                {
                    var td = md.GetTypeDefinition(tdHandle);
                    typeName = md.GetString(td.Namespace) + "." + md.GetString(td.Name);
                    if (typeName.StartsWith(".")) typeName = typeName.Substring(1);
                }
            }
            catch { }

            string methodName = md.GetString(mdef.Name);

            if (filter != null && typeName.IndexOf(filter, StringComparison.OrdinalIgnoreCase) < 0)
                continue;

            // 解析方法头：tiny(1 字节) / fat(12 字节)
            int hs, ilsz;
            try
            {
                var blob = peReader.GetSectionData(rva);
                byte b0 = blob.GetContent().Length > 0 ? blob.GetContent()[0] : (byte)0;
                if ((b0 & 0x3) == 0x2) { hs = 1; ilsz = b0 >> 2; }
                else
                {
                    hs = 12;
                    var rd = blob.GetReader();
                    rd.ReadByte();
                    rd.ReadByte();
                    rd.ReadUInt16();      // flags/size
                    ilsz = rd.ReadInt32();
                }
            }
            catch { continue; }

            long ilStart = baseAddr + rva + hs;
            Entry e = null;
            // 注意: 表的 key 是 boxed Int64（不是 IntPtr），用 IntPtr 去查永远不命中
            foreach (long cand in new[] { ilStart, baseAddr + rva, baseAddr + rva + 1, baseAddr + rva + 12 })
            {
                if (!tbl.ContainsKey(cand)) continue;
                e = ReadEntry(tbl[cand]);
                if (e != null) break;
            }
            if (e == null) { miss++; continue; }

            byte[] buf = new byte[e.Size];
            Marshal.Copy(e.Dec, buf, 0, e.Size);
            string fname = "m_" + token.ToString("x8") + ".il";
            File.WriteAllBytes(Path.Combine(ilDir, fname), buf);

            // tok, rva, headerSize, ilSize, headerHex, ilFile
            var head = new byte[hs];
            Array.Copy(bytes, RvaToOffset(peReader, rva), head, 0, hs);
            manifest.Append(token.ToString("x8")).Append('\t')
                    .Append(rva).Append('\t')
                    .Append(hs).Append('\t')
                    .Append(e.Size).Append('\t')
                    .Append(BitConverter.ToString(head).Replace("-", "")).Append('\t')
                    .Append(fname).Append('\n');
            hit++;
        }

        File.WriteAllText(Path.Combine(outDir, "manifest.tsv"), manifest.ToString(), new UTF8Encoding(false));
        Console.WriteLine($"dumped {hit} method bodies, missed {miss}");
        Console.WriteLine("out: " + outDir);
        return hit > 0 ? 0 : 5;
    }

    static int RvaToOffset(PEReader pe, int rva)
    {
        foreach (var s in pe.PEHeaders.SectionHeaders)
        {
            int size = Math.Max(s.VirtualSize, s.SizeOfRawData);
            if (rva >= s.VirtualAddress && rva < s.VirtualAddress + size)
                return s.PointerToRawData + (rva - s.VirtualAddress);
        }
        return 0;
    }

    static int List(Assembly asm)
    {
        var tbl = FindTable(asm);
        if (tbl == null) { Console.WriteLine("no table"); return 4; }
        Console.WriteLine("table entries: " + tbl.Count);
        int n = 0;
        foreach (DictionaryEntry de in tbl)
        {
            var e = ReadEntry(de.Value);
            if (e == null) continue;
            Console.WriteLine($"  0x{Convert.ToInt64(de.Key):X}  size={e.Size}");
            if (++n >= 10) break;
        }
        return 0;
    }

    // ---------------- 字符串常量 ----------------

    static int Strings(Assembly asm, string[] args)
    {
        // Reactor 的字符串解密函数：static string f(int offset)
        MethodInfo dec = null;
        foreach (var t in SafeTypes(asm))
        {
            MethodInfo[] ms;
            try { ms = t.GetMethods(BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic); }
            catch { continue; }
            if (t.FullName == null || !t.FullName.StartsWith("GuwBZAWohQ92tIs0TO")) continue;
            foreach (var m in ms)
            {
                var ps = m.GetParameters();
                if (m.ReturnType == typeof(string) && ps.Length == 1 &&
                    (ps[0].ParameterType == typeof(int) || ps[0].ParameterType == typeof(uint)))
                { dec = m; break; }
            }
            if (dec != null) break;
        }
        if (dec == null)
        {
            Console.Error.WriteLine("string decryptor not found (混淆器命名空间可能不同)");
            return 6;
        }
        for (int i = 2; i < args.Length; i++)
        {
            int off = Convert.ToInt32(args[i], 16);
            object r = dec.Invoke(null, new object[] { off });
            Console.WriteLine($"0x{off:X8} -> \"{r}\"");
        }
        return 0;
    }
}
