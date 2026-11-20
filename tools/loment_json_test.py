#!/usr/bin/env python3
# loment_json_test.py — Loment 版 JSON 库与 Python json 行为一致 (docs/148 §2b)
#
# 判据: 探针程序里嵌若干 payload, 用 loment/lib/json.lomt 解析 + 取值 + 生成, stdout 必须与
# 用 Python `json` 算出来的**逐字节相同** —— 这样 Loment 版 LSP 的 JSON-RPC 帧才能与
# Python 版对齐。
#
# 覆盖: 嵌套对象/数组、空对象/空数组、字符串转义 (\r \n \t \\ \")、\uXXXX 与代理对、
#       整数/负数/大数、true/false/null、"值里含 \"method\" 字面量" 不会被误当成键。
#
# 运行: python tools/loment_json_test.py   (无 clang/WSL 时 SKIP, 退出码 0)

from __future__ import annotations

import os

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

#: WSL 侧临时路径前缀 —— **每个进程一份**。WSL 的 `/tmp` 是所有 `wsl -e` 调用
#: 共用的, 固定文件名在**并发跑门禁**时会让两个进程互相跑对方的二进制 ——
#: 那是**错结果**, 不是慢。见 `ci.py` 的 `-j`。
_T = f"/tmp/loment-{os.getpid()}-"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def _clang() -> str | None:
    p = shutil.which("clang")
    if p:
        return p
    fb = r"C:\Program Files\LLVM\bin\clang.exe"
    return fb if Path(fb).exists() else None


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


def _wsl() -> bool:
    if sys.platform.startswith("linux"):
        return True
    if not shutil.which("wsl"):
        return False
    try:
        return subprocess.run([*_WSLCMD, "true"], capture_output=True, text=True,
                              timeout=60, shell=False).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    if sys.platform.startswith("linux"):
        return s
    return "/mnt/" + s[0].lower() + s[2:]


# ---------------------------------------------------------------- payload 与期望

PAYLOADS = [
    # 0: didOpen 的真实形状 —— 文档文本里有 \r\n 与转义引号
    '{"jsonrpc":"2.0","id":7,"method":"textDocument/didOpen",'
    '"params":{"textDocument":{"uri":"file:///x.lomt",'
    '"text":"fn f() {\\r\\n    let s: str = \\"hi\\\\n\\";\\r\\n}\\r\\n"}}}',
    # 1: 数组/空容器/布尔/null/负数/大数
    '{"a":[true,false,null,0,-5,123456789],"b":{"c":{},"d":[]}}',
    # 2: \uXXXX (BMP) 与代理对 (emoji)
    '{"u":"\\u4e2d\\u6587 \\ud83d\\ude00 x"}',
    # 3: 各种转义
    '{"k":"line1\\nline2\\ttab\\\\slash\\"q"}',
    # 4: 两层嵌套
    '{"nested":{"deep":{"leaf":"v"}}}',
    # 5: 值里含 "method" 字面量: 解析器不能被它骗到
    '{"method":"x","params":{"text":"\\"method\\": \\"fake\\""}}',
]


def _lom_str(s: str) -> str:
    """把 Python 字符串写成 Loment 字面量 (转义 \\ 与 ")。"""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


PROBE_TMPL = '''// loment/build/json_probe.lomt — JSON 库的探针 (由 tools/loment_json_test.py 生成)
module json_probe
choose runtime

use "loment/lib/json.lomt"

fn wr(fd: u64, p: ptr, n: u32) -> i64 {
    return syscall4(1, fd, p as u64, n as u64);
}

fn pl(s: str) -> i64 {
    return wr(1, str_ptr(s), str_len(s));
}

fn pn(v: u32) -> i64 {
    let buf: ptr = alloc(16);
    let d: u32 = 0;
    let x: u32 = v;
    if x == 0 {
        store8(buf, 0, 48 as u8);
        d = 1;
    }
    while x > 0 {
        store8(buf, d, (48 + x % 10) as u8);
        x = x / 10;
        d = d + 1;
    }
    let q: u32 = 0;
    let tmp: ptr = alloc(d);
    while q < d {
        store8(tmp, q, load8(buf, d - 1 - q) as u8);
        q = q + 1;
    }
    return wr(1, tmp, d);
}

fn kv(k: str, v: u32) -> i64 {
    pl(k);
    return pn(v);
}

fn ks(k: str, js: ptr, arena: ptr, n: u32) -> i64 {
    pl(k);
    let buf: ptr = alloc(2048);
    let m: u32 = json_str_into(js, arena, n, buf, 2048);
    wr(1, buf, m);
    return pl("\\n");
}

fn _start() {
    let arena: ptr = alloc(16384);
    let out: ptr = alloc(4096);
    __PAYLOADS__
    // ---- 生成侧: 与 json.dumps(ensure_ascii=False) 对齐
    pl("--emit\\n");
    let o: u32 = 0;
    o = json_emit_lit(out, o, "{\\"jsonrpc\\": \\"2.0\\", \\"id\\": ");
    o = json_emit_u32(out, o, 7);
    o = json_emit_lit(out, o, ", \\"result\\": {\\"ok\\": true, \\"n\\": ");
    o = json_emit_u32(out, o, 42);
    o = json_emit_lit(out, o, ", \\"s\\": ");
    let raw: str = "a\\"b\\n中\\tz";
    o = json_emit_str(out, o, str_ptr(raw), str_len(raw));
    o = json_emit_lit(out, o, ", \\"arr\\": [1, 2]}}");
    wr(1, out, o);
    pl("\\n");
    syscall4(60, 0, 0, 0);
}
'''


def _payload_block(idx: int, payload: str) -> str:
    """每个 payload: 解析 -> 取几个字段打出来。"""
    return f'''
    pl("--payload {idx}\\n");
    let js{idx}: str = {_lom_str(payload)};
    let root{idx}: u32 = json_parse(str_ptr(js{idx}), str_len(js{idx}), arena);
    kv("root_kind=", json_nkind(arena, root{idx}));
    pl("\\n");
'''


def _build(td: Path) -> Path:
    probe = ROOT / "loment" / "build" / "json_probe.lomt"
    probe.parent.mkdir(parents=True, exist_ok=True)
    src = PROBE_TMPL.replace("__PAYLOADS__", "".join(
        _payload_block(i, p) for i, p in enumerate(PAYLOADS)))
    with probe.open("w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    try:
        return _compile(probe, td, "json_probe")
    finally:
        probe.unlink(missing_ok=True)


def _compile(probe: Path, td: Path, name: str) -> Path:
    import lomentc
    mod = lomentc.load(probe)
    deps = lomentc.resolve_deps(mod, ROOT, probe.parent, entry=probe)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"探针自己检查不过: {errs[:3]}"
    ll = td / f"{name}.ll"
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    elf = td / f"{name}.elf"
    r = subprocess.run(
        [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    return elf


def _run(elf: Path, td: Path) -> str:
    got = td / "probe.out"
    script = (f"rm -f {_T}json_probe.bin && cp {_wsl_path(elf)} {_T}json_probe.bin && "
              f"chmod +x {_T}json_probe.bin && "
              f"{_T}json_probe.bin > {_wsl_path(got)}; echo -n $?")
    r = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                       capture_output=True, text=True, timeout=300, shell=False)
    assert r.stdout.strip() == "0", f"探针退出码 {r.stdout!r}"
    # 按**原始字节**读: read_text 会把 \r\n 归一成 \n, 那样"解码出的 CR"就看不出来了
    return got.read_bytes().decode("utf-8")


@test
def test_json_library_matches_python():
    """解析侧: 每个 payload 的根节点类型 + 关键字段取值; 生成侧: 与 json.dumps 一致。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        got = _run(elf, td)

    want: list[str] = []
    for i, p in enumerate(PAYLOADS):
        want.append(f"--payload {i}")
        # **用 `_kind_of`, 不要写 `json.loads(p) and 1`** —— 后者对任何非空 JSON 恒等于 1,
        # 于是这一条只断言"根非空", 而 docstring 说的是"根节点类型"（2026-09-22 第三方复核
        # 抓到）。`_kind_of` 与 `json.lomt` 的 kind 同表, 就在本文件 380 行。
        want.append(f"root_kind={_kind_of(json.loads(p))}")
    want.append("--emit")
    want.append(json.dumps({"jsonrpc": "2.0", "id": 7,
                            "result": {"ok": True, "n": 42, "s": 'a"b\n中\tz',
                                       "arr": [1, 2]}}, ensure_ascii=False))
    # 生成侧的行没有换行结尾差异: 拼接后逐字节比
    gl = got.split("\n")
    wl = want
    assert gl[:len(wl)] == wl, (
        "逐行不一致:\n  got : " + repr(gl[:len(wl)]) + "\n  want: " + repr(wl))


@test
def test_json_decoding_and_lookup():
    """取值侧: 解码后的文本 / 数字 / 布尔 / 嵌套查找 / "值里含键名" 不误判。

    这一条用**独立的探针**只做取值, 便于把期望写成 Python 的 json 结果。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    probe = ROOT / "loment" / "build" / "json_lookup.lomt"
    probe.parent.mkdir(parents=True, exist_ok=True)
    p0, p2, p5 = PAYLOADS[0], PAYLOADS[2], PAYLOADS[5]
    src = f'''module json_lookup
choose runtime

use "loment/lib/json.lomt"

fn wr(fd: u64, p: ptr, n: u32) -> i64 {{
    return syscall4(1, fd, p as u64, n as u64);
}}

fn pl(s: str) -> i64 {{
    return wr(1, str_ptr(s), str_len(s));
}}

fn pn(v: u32) -> i64 {{
    let buf: ptr = alloc(16);
    let d: u32 = 0;
    let x: u32 = v;
    if x == 0 {{
        store8(buf, 0, 48 as u8);
        d = 1;
    }}
    while x > 0 {{
        store8(buf, d, (48 + x % 10) as u8);
        x = x / 10;
        d = d + 1;
    }}
    let q: u32 = 0;
    let tmp: ptr = alloc(16);
    while q < d {{
        store8(tmp, q, load8(buf, d - 1 - q) as u8);
        q = q + 1;
    }}
    return wr(1, tmp, d);
}}

fn _start() {{
    let arena: ptr = alloc(16384);
    let buf: ptr = alloc(4096);
    // 0) 文档文本: 解码后的 UTF-8 (含 \\r\\n 与转义引号)
    let j0: str = {_lom_str(p0)};
    let r0: u32 = json_parse(str_ptr(j0), str_len(j0), arena);
    let params: u32 = json_get(str_ptr(j0), arena, r0, "params");
    let td: u32 = json_get(str_ptr(j0), arena, params, "textDocument");
    let uri: u32 = json_get(str_ptr(j0), arena, td, "uri");
    let text: u32 = json_get(str_ptr(j0), arena, td, "text");
    let m: u32 = json_str_into(str_ptr(j0), arena, uri, buf, 4096);
    wr(1, buf, m);
    pl("\\n");
    m = json_str_into(str_ptr(j0), arena, text, buf, 4096);
    wr(1, buf, m);
    pl("\\n");
    // 0b) id / 字符串长度要靠 as_u32 与 str_into 各自正确
    pn(json_as_u32(str_ptr(j0), arena, json_get(str_ptr(j0), arena, r0, "id")));
    pl("\\n");
    // 2) \\uXXXX 与代理对
    let j2: str = {_lom_str(p2)};
    let r2: u32 = json_parse(str_ptr(j2), str_len(j2), arena);
    m = json_str_into(str_ptr(j2), arena, json_get(str_ptr(j2), arena, r2, "u"), buf, 4096);
    wr(1, buf, m);
    pl("\\n");
    // 5) 值里含 "method" 字面量: 取 "method" 必须是 "x"
    let j5: str = {_lom_str(p5)};
    let r5: u32 = json_parse(str_ptr(j5), str_len(j5), arena);
    m = json_str_into(str_ptr(j5), arena,
                      json_get(str_ptr(j5), arena, r5, "method"), buf, 4096);
    wr(1, buf, m);
    pl("\\n");
    let tx: u32 = json_get(str_ptr(j5), arena,
                           json_get(str_ptr(j5), arena, r5, "params"), "text");
    m = json_str_into(str_ptr(j5), arena, tx, buf, 4096);
    wr(1, buf, m);
    pl("\\n");
    syscall4(60, 0, 0, 0);
}}
'''
    with probe.open("w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    try:
        with tempfile.TemporaryDirectory() as tds:
            td = Path(tds)
            import lomentc
            mod = lomentc.load(probe)
            deps = lomentc.resolve_deps(mod, ROOT, probe.parent, entry=probe)
            errs = lomentc.check(mod, deps=deps)
            assert not errs, f"取值探针检查不过: {errs[:3]}"
            ll = td / "lookup.ll"
            with ll.open("w", encoding="utf-8", newline="\n") as f:
                f.write(lomentc.emit_llvm(mod, ROOT, deps))
            elf = td / "lookup.elf"
            r = subprocess.run(
                [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib",
                 "-ffreestanding", "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
                capture_output=True, text=True, shell=False)
            assert r.returncode == 0, r.stderr[-400:]
            got = _run(elf, td)
    finally:
        probe.unlink(missing_ok=True)

    d0 = json.loads(p0)
    d2 = json.loads(p2)
    d5 = json.loads(p5)
    want = "\n".join([
        d0["params"]["textDocument"]["uri"],
        d0["params"]["textDocument"]["text"],
        str(d0["id"]),
        d2["u"],
        d5["method"],
        d5["params"]["text"],
    ]) + "\n"
    assert got == want, f"\n  got : {got!r}\n  want: {want!r}"


# ---------------------------------------------------------------- Loment 版（S1 第七格）

TWIN = ROOT / "loment" / "tools" / "lomjsoncheck.lomt"

#: 观测点 —— **两侧同一份清单**（孪生那边逐条写死, 这边是同一套路径）。
_OBS: dict[int, list[tuple[str, list[str]]]] = {
    0: [("params.textDocument.uri", ["params", "textDocument", "uri"]),
        ("params.textDocument.text", ["params", "textDocument", "text"])],
    1: [("a", ["a"]), ("b.c", ["b", "c"]), ("b.d", ["b", "d"])],
    2: [("u", ["u"])],
    3: [("k", ["k"])],
    4: [("nested", ["nested"]), ("nested.deep", ["nested", "deep"]),
        ("nested.deep.leaf", ["nested", "deep", "leaf"])],
    5: [("method", ["method"]), ("params.text", ["params", "text"])],
}
#: payload 1 里 `a` 的元素个数（孪生那边是 `while i < 6`）。
_A_LEN = 6


def _kind_of(v: object) -> int:
    """与 `json.lomt` 的 kind 同表: 1 obj / 2 arr / 3 str / 4 num / 5 true / 6 false / 7 null。

    **`bool` 要在 `int` 前面判** —— Python 里 `isinstance(True, int)` 是 True。
    """
    if isinstance(v, dict):
        return 1
    if isinstance(v, list):
        return 2
    if isinstance(v, str):
        return 3
    if isinstance(v, bool):
        return 5 if v else 6
    if v is None:
        return 7
    if isinstance(v, int):
        return 4
    raise AssertionError(f"没见过的 JSON 值类型: {type(v)}")


def _text_of(v: object) -> str:
    """与孪生 `node_text` 同一套规则: str 取解码后的、num 取原文、三种字面量取规范拼法。"""
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, int):
        return str(v)
    return ""          # obj / arr


def _expected_json_report() -> str:
    """用 CPython `json` 独立算一遍孪生该打的报告（**不复用探针那条路**）。"""
    out: list[str] = []
    for i, p in enumerate(PAYLOADS):
        d = json.loads(p)
        out.append(f"P{i} root_kind {_kind_of(d)}")
        for label, path in _OBS[i]:
            v: object = d
            for k in path:
                v = v[k]           # type: ignore[index]
            out.append(f"  {label} {_kind_of(v)} {_text_of(v)}")
            if label == "a":
                for j in range(_A_LEN):
                    e = v[j]       # type: ignore[index]
                    out.append(f"  a{j} {_kind_of(e)} {_text_of(e)}")
    obj = {"jsonrpc": "2.0", "id": 7,
           "result": {"ok": True, "n": 42, "s": 'a"b\n中\tz', "arr": [1, 2]}}
    out.append("EMIT " + json.dumps(obj, ensure_ascii=False))
    return "\n".join(out) + "\n"


@test
def test_json_check_matches_loment_twin():
    """**Loment 版**（直接 `use json`, 不经探针）与 CPython `json` 的观察**逐字节相同**。

    `docs/189` §3 的 S1 第七格。这一格顺带说明一件事: 参考实现那侧要**生成一份探针源**
    再编出来跑（Python 调不了 Loment 的库）, 而 Loment 侧**不要那一层** —— 它自己就是
    Loment 程序, 直接调 `loment/lib/json.lomt`。
    """
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    import lomentc
    want = _expected_json_report()
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        mod = lomentc.load(TWIN)
        deps = lomentc.resolve_deps(mod, ROOT, TWIN.parent, entry=TWIN)
        errs = lomentc.check(mod, deps=deps)
        assert not errs, f"lomjsoncheck.lomt 自己检查不过: {errs[:3]}"
        ll = td / "lomjsoncheck.ll"
        with ll.open("w", encoding="utf-8", newline="\n") as f:
            f.write(lomentc.emit_llvm(mod, ROOT, deps))
        elf = td / "lomjsoncheck.elf"
        r = subprocess.run(
            [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
             "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
            capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-400:]
        got = td / "twin.out"
        binn = f"{_T}jsoncheck.bin"
        script = (f"rm -f {binn} && cp {_wsl_path(elf)} {binn} && chmod +x {binn} && "
                  f"cd {_wsl_path(ROOT)} && {binn} > {_wsl_path(got)}; echo -n $?")
        rr = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                            capture_output=True, text=True, timeout=300, shell=False)
        assert rr.stdout.strip() == "0", f"孪生退出码 {rr.stdout!r}"
        # 按**原始字节**读: read_text 会把 \r\n 归一成 \n, 那样解码出的 CR 就看不出来了
        out = got.read_bytes().decode("utf-8")
    assert out == want, f"不一致:\n  got : {out!r}\n  want: {want!r}"
    # 独立期望值: 解码侧那几个"字符串字面量装不下"的字节, 一个都不能少
    assert "\r" in out and "\t" in out and "中" in out and "\U0001f600" in out, repr(out[-200:])
    print(f"      {len(PAYLOADS)} 个 payload + 生成侧: 与 CPython json 逐字节相同")


@test
def test_json_twin_selfhost_compiles():
    """`lomjsoncheck.lomt` 必须能走**种子自举链**编译（无 Python 参与编译器本身）。"""
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    assert seed.exists(), "缺自举种子"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        s1 = td / "stage1"
        r = subprocess.run(
            [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
             "-static", "-fuse-ld=lld", "-o", str(s1), str(seed)],
            capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-300:]
        binn = f"{_T}jsoncheck_s1.bin"
        script = (f"cp {_wsl_path(s1)} {binn} && chmod +x {binn} && "
                  f"cd {_wsl_path(ROOT)} && {binn} loment/tools/lomjsoncheck.lomt")
        rr = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                            capture_output=True, timeout=600, shell=False)
        assert rr.returncode == 0, f"stage1 编译 lomjsoncheck.lomt 失败: {rr.stderr[-300:]}"
        assert len(rr.stdout) > 20000, f"产物太小 ({len(rr.stdout)}B)"
    print(f"      种子自举链编译 lomjsoncheck.lomt 成功 ({len(rr.stdout)}B IR)")


def main() -> int:
    return loment_suite.run(TESTS, "loment_json_test")


if __name__ == "__main__":
    sys.exit(main())
