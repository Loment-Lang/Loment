#!/usr/bin/env python3
"""loment_lomtfrom_test.py — **Potato 对象 -> L1 接口单元** 的 Loment 版（docs/179 / 189）。

`tools/lomt_from.py::emit_lomt(doc)` —— 多语法前端那条链的**后半段**：

    任意源语法  --potato_from-->  Potato 形式对象  --lomt_from-->  .lomt

前半段（`potato_from` 的五门）已经有了 Loment 版（`lompotc.lomt`，第 16 格）；
这一段是**发射那一半**，判据与前几格**同一条**：同一份对象喂两边，产出的源码逐字节相同。

## 这一格比的是文本，且**只走 `impl=False`**

`--impl`（把带正文的函数交给 `ctrans`/`pytrans` 翻成真实现）要的是**整条语法树那条路**
（`trans_core` + 六门方言），那是 §4.1 里另一根轴 —— 孪生那边明确不做（文件头写着）。
所以这一格覆盖的是**接口单元**那条路：函数一律 `pub extern fn`，没有正文的函数报 `[skip]`。

## 覆盖面

* `loment/build/*.potato.json` —— 仓库里**已提交**的那批对象（编译器模块自己的）；
* 五门语料经 `potato_from` 转出来的对象（带 caps / consts / types / functions 的真形状）；
* `_BATTERY` 那批手写的对象 —— 每一份钉一条规则或一个**错误类别**（退出码 1）。

## 比什么

1. **stdout 逐字节相同**（生成的 `.lomt` 源码）；
2. **退出码相同**（0 = 发出来了 / 1 = 表示面之外）；
3. **跳过项按名字都对上了** —— 孪生的 stderr 里每个 `skip <名字>` 都必须对应一个
   "Python 那边没发出来的函数"，反之亦然。跳过**不出声**就等于让用户以为那个函数
   本来就不在那儿（`lomt_from.main` 里那段注解）。类别（为什么跳）不比 —— 那要复现
   Python 的文案，与第 15 格 `lompotato` 同一条纪律。

用法: python tools/loment_lomtfrom_test.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402

import lomelf  # noqa: E402
import lomentc  # noqa: E402
import lomt_from  # noqa: E402
import potato_from  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TWIN = ROOT / "loment" / "tools" / "lomtfrom.lomt"

TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


def _base(**kw) -> dict:
    """一份**合法**对象（v6）。各条用例只在它上面改一处。"""
    d = {"potato": "v6", "unit": "u", "language": "loment", "grammar": "c",
         "imports": [], "capabilities": [], "functions": [], "layouts": [],
         "consts": [], "enums": [], "types": [], "traits": [], "impls": [],
         "generics": [], "instances": [], "guards": 0, "excluded": [],
         "mode": "std", "switches": [], "dialects": [], "bodies": []}
    d.update(kw)
    return d


def _fn(**kw) -> dict:
    e = {"name": "f", "params": [], "ret": "i32", "abi": "c"}
    e.update(kw)
    return e


#: 判据自己带的那批对象 —— 每一份钉一条规则（正例）或一个**错误类别**（反例）。
_BATTERY = {
    # ---- 能力域
    "cap_plain": _base(capabilities=[{"name": "CAP", "domain": {"lo": 1, "hi": 16, "space": "dom"}}]),
    "cap_neg_lo": _base(capabilities=[{"name": "CAP", "domain": {"lo": -5, "hi": 0, "space": "d"}}]),
    "cap_revocable": _base(capabilities=[{"name": "CAP", "domain": {"lo": 0, "hi": 7, "space": "d"},
                                          "revocable": True}]),
    "cap_two": _base(capabilities=[{"name": "A", "domain": {"lo": 0, "hi": 1, "space": "d"}},
                                   {"name": "B", "domain": {"lo": 2, "hi": 3, "space": "d"}}]),
    "cap_badname": _base(capabilities=[{"name": "9x", "domain": {"lo": 0, "hi": 1, "space": "d"}}]),
    "cap_float_lo": _base(capabilities=[{"name": "C", "domain": {"lo": 1.5, "hi": 1, "space": "d"}}]),
    "cap_no_dom": _base(capabilities=[{"name": "C", "domain": {}}]),
    "cap_badspace": _base(capabilities=[{"name": "C", "domain": {"lo": 0, "hi": 1, "space": "1x"}}]),
    "cap_no_space": _base(capabilities=[{"name": "C", "domain": {"lo": 0, "hi": 1}}]),
    # ---- 常量
    "const_ok": _base(consts=[{"name": "K", "type": "i32", "value": 5}]),
    "const_neg": _base(consts=[{"name": "K", "type": "i32", "value": -12}]),
    "const_big": _base(consts=[{"name": "K", "type": "i64", "value": 4294967296}]),
    "const_float": _base(consts=[{"name": "K", "type": "i32", "value": 1.5}]),
    "const_badname": _base(consts=[{"name": "1K", "type": "i32", "value": 1}]),
    "const_empty_type": _base(consts=[{"name": "K", "type": "", "value": 1}]),
    # ---- 结构体
    "struct_ok": _base(types=[{"name": "P", "fields": [{"name": "x", "type": "i32"},
                                                       {"name": "y", "type": "u8"}]}]),
    "struct_two": _base(types=[{"name": "P", "fields": [{"name": "x", "type": "i32"}]},
                               {"name": "Q", "fields": [{"name": "y", "type": "i32"}]}]),
    "struct_empty": _base(types=[{"name": "P", "fields": []}]),
    "struct_dup": _base(types=[{"name": "P", "fields": [{"name": "x", "type": "i32"},
                                                        {"name": "x", "type": "u8"}]}]),
    "struct_badfield": _base(types=[{"name": "P", "fields": [{"name": "1x", "type": "i32"}]}]),
    "struct_empty_type": _base(types=[{"name": "P", "fields": [{"name": "x", "type": ""}]}]),
    "struct_badname": _base(types=[{"name": "1P", "fields": [{"name": "x", "type": "i32"}]}]),
    # ---- 枚举（变体是**裸字符串**）
    "enum_ok": _base(enums=[{"name": "E", "variants": ["A", "B"]}]),
    "enum_payload": _base(enums=[{"name": "E", "variants": ["A", "B"],
                                  "payloads": {"A": "i32"}}]),
    "enum_empty": _base(enums=[{"name": "E", "variants": []}]),
    "enum_dup": _base(enums=[{"name": "E", "variants": ["A", "A"]}]),
    "enum_badvariant": _base(enums=[{"name": "E", "variants": ["1A"]}]),
    # ---- 函数
    "fn_ok": _base(functions=[_fn(params=[{"name": "a", "type": "i32"}])]),
    "fn_void": _base(functions=[_fn(ret="()")]),
    "fn_no_ret_key": _base(functions=[{"name": "f", "params": [], "abi": "c"}]),
    "fn_ptr": _base(functions=[_fn(params=[{"name": "p", "type": "ptr"}], ret="ptr")]),
    "fn_two": _base(functions=[_fn(), _fn(name="g", params=[{"name": "b", "type": "u8"}])]),
    "fn_loment_no_body": _base(functions=[{"name": "f", "params": [], "ret": "i32"}]),
    "fn_abi_python": _base(functions=[_fn(abi="python")]),
    "fn_abi_c_missing": _base(functions=[_fn(abi=None)]),
    "fn_param_str": _base(functions=[_fn(params=[{"name": "a", "type": "str"}])]),
    "fn_ret_str": _base(functions=[_fn(ret="str")]),
    "fn_bad_param_name": _base(functions=[_fn(params=[{"name": "1a", "type": "i32"}])]),
    "fn_dup": _base(functions=[_fn(), _fn(params=[{"name": "b", "type": "i32"}])]),
    "fn_badname": _base(functions=[_fn(name="1f")]),
    "fn_mixed_skip": _base(functions=[_fn(), {"name": "g", "params": [],
                                              "abi": "c", "ret": "str"},
                                       {"name": "h", "params": [], "ret": "i32"}]),
    # ---- 出界声明
    "excluded_ok": _base(excluded=["a", "b c"]),
    "excluded_num": _base(excluded=[5]),
    "excluded_empty": _base(excluded=[""]),
    # ---- 表示面之外的那几个键
    "traits_nonempty": _base(traits=[{"name": "T"}]),
    "impls_nonempty": _base(impls=[{"name": "T"}]),
    "generics_nonempty": _base(generics=[{"name": "G"}]),
    "instances_nonempty": _base(instances=[{"name": "G"}]),
    "layouts_nonempty": _base(layouts=[{"name": "H"}]),
    "imports_nonempty": _base(imports=["other"]),
    "traits_empty_ok": _base(traits=[]),
    # ---- unit / 头部
    "unit_bad": _base(unit="1u"),
    "unit_missing": {k: v for k, v in _base().items() if k != "unit"},
    "lang_missing": {k: v for k, v in _base().items() if k != "language"},
    "potato_old": dict(_base(), potato="v2"),
    "empty_object": _base(),
    # ---- 全都来一点
    "mixed": _base(capabilities=[{"name": "CAP", "domain": {"lo": 1, "hi": 16, "space": "dom"}}],
                   consts=[{"name": "K", "type": "i32", "value": -3}],
                   types=[{"name": "P", "fields": [{"name": "x", "type": "i32"}]}],
                   enums=[{"name": "E", "variants": ["A"], "payloads": {"A": "u8"}}],
                   functions=[_fn(), {"name": "g", "params": [], "ret": "i32"},
                              {"name": "h", "params": [{"name": "p", "type": "str"}],
                               "abi": "c"}],
                   excluded=["x"]),
}


def _cases() -> list[tuple[str, dict]]:
    """对象集: 仓库里已提交的那些 + 五门语料转出来的 + 自带的那批。"""
    cases: list[tuple[str, dict]] = []
    for f in sorted((ROOT / "loment" / "build").glob("*.potato.json")):
        cases.append((f.name, json.loads(f.read_text(encoding="utf-8"))))
    for d, fn, ext in (("ctrans", potato_from.from_c, ".c"),
                       ("cpptrans", potato_from.from_cpp, ".cpp"),
                       ("jtrans", potato_from.from_java, ".java"),
                       ("cstrans", potato_from.from_csharp, ".cs"),
                       ("gotrans", potato_from.from_go, ".go")):
        for f in sorted((ROOT / "loment" / d).glob("*" + ext)):
            cases.append((f"{d}/{f.name}",
                          fn(f.read_text(encoding="utf-8"), f.name, "strict")[0]))
    cases += [(k, v) for k, v in sorted(_BATTERY.items())]
    return cases


def _twin_exe(td: Path) -> Path:
    mod = lomentc.load(TWIN)
    deps = lomentc.resolve_deps(mod, ROOT, TWIN.parent, entry=TWIN)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"lomtfrom.lomt 自己检查不过: {errs[:2]}"
    ir = lomentc.emit_llvm(mod, ROOT, deps)
    exe = td / ("lomtfrom.exe" if os.name == "nt" else "lomtfrom")
    exe.write_bytes((lomelf.compile_pe(ir) if os.name == "nt" else lomelf.compile_ll(ir))[0])
    exe.chmod(0o755)
    return exe


@test
def test_lomtfrom_twin_matches_emit_lomt():
    """**发射那一半也有 Loment 版了**（S1 第十七格）。

    同一份形式对象喂两边，比 **stdout 逐字节相同 + 退出码相同 + 跳过项按名字对上**。
    覆盖面见 `_cases()`。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        exe = _twin_exe(td)
        fp = td / "o.json"
        cases = _cases()
        bad = []
        for name, doc in cases:
            fp.write_text(json.dumps(doc, ensure_ascii=False),
                          encoding="utf-8", newline="\n")
            r = subprocess.run([str(exe), str(fp)], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", shell=False,
                               timeout=120)
            try:
                want, skipped = lomt_from.emit_lomt(doc)
                wrc = 0
            except lomt_from.NotRepresentable:
                want, skipped, wrc = "", [], 1
            # 跳过项：孪生 stderr 里那些 `skip <名字>` 必须与"没发出来的函数"一一对上
            got_skips = sorted(re.findall(r"(?m)^skip (\S+) ", r.stderr))
            want_skips = sorted(n for n, _ in skipped)
            if r.returncode != wrc or r.stdout != want or got_skips != want_skips:
                i = 0
                n = min(len(r.stdout), len(want))
                while i < n and r.stdout[i] == want[i]:
                    i += 1
                bad.append((name, r.returncode, wrc, want[max(0, i - 60):i + 80],
                            r.stdout[max(0, i - 60):i + 80], want_skips, got_skips))
        assert not bad, (
            f"{len(bad)}/{len(cases)} 份与 emit_lomt 不同（前 2）:\n"
            + "\n".join(f"  {n}: rc={rc}/{wrc}\n    py={w!r}\n    tw={g!r}\n"
                        f"    skips py={ws} tw={gs}"
                        for n, rc, wrc, w, g, ws, gs in bad[:2]))
        print(f"      {len(cases)} 份对象：Loment 版发射器与 `lomt_from.emit_lomt` "
              f"产出的源码逐字节相同，退出码与跳过项也一致")


@test
def test_lomtfrom_twin_selfhost_compiles():
    """`lomtfrom.lomt` 必须能走**种子自举链**编译，且产出的 IR 与参考实现**逐字节相同**。"""
    import loment_dist  # noqa: E402

    stage1 = loment_dist.build_stage1()
    mod = lomentc.load(TWIN)
    deps = lomentc.resolve_deps(mod, ROOT, TWIN.parent, entry=TWIN)
    want = lomentc.emit_llvm(mod, ROOT, deps)
    r = subprocess.run([str(stage1), TWIN.relative_to(ROOT).as_posix()], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=False, timeout=600)
    assert r.returncode == 0, f"stage1 编译 lomtfrom.lomt 失败: {r.stderr[-400:]}"
    got = r.stdout.replace("\r\n", "\n")
    assert got == want, f"自举镜与参考的 IR 不一致 (want {len(want)}B got {len(got)}B)"
    print(f"      种子自举链编译 lomtfrom.lomt 成功，且 IR 与参考逐字节相同 ({len(want)}B)")


@test
def test_externs_keys_are_the_declared_names():
    """`emit_lomt(impl=True)` 递给翻译器的 `externs` 表，**每个键必须是那条声明自己的名字**。

    `docs/186` §9 那条"声明在此、实现在别处"的口子就是它：带正文的函数体里调用一个
    **本单元没有正文**的函数时，靠这张表让调用点解析得到。

    曾经写成 `{n: _ty(...) for f in fns ...}` —— 而 `n` 不是这个推导式的变量，它是上面
    `for f in fns:` 那个循环**残留下来**的最后一个函数名。于是每个条目的键都塌成同一个
    名字、表只剩一条，调用点解析不到，报出来的却是"本单元没有这个函数"（指向别处）。

    这一条**不在孪生的覆盖面上** —— `lomtfrom.lomt` 只走 `impl=False` 那条路
    （它的头注写明），所以只在参考实现这边钉。
    """
    doc = _base(functions=[
        # 无正文 + `abi="c"` = 外部函数：发 `pub extern fn`，并进 `externs` 表
        _fn(name="helper", params=[{"name": "x", "type": "i32"}], ret="i32"),
        # 有正文的那个调用它
        _fn(name="main", abi=None, body="int main() { return helper(3); }", body_line=3),
    ])
    text, skipped = lomt_from.emit_lomt(doc, impl=True)
    assert not skipped, f"没有东西该被跳过，却跳了：{skipped}"
    assert "pub extern fn helper" in text, text
    assert "helper(3)" in text, text
    print("      externs 的键是声明自己的名字；调用点因此解析得到")


@test
def test_cli_without_lang_reads_a_source_file_not_json():
    """不给 `--lang` 时**按输入的性质分**：`.json` 是形式对象，别的都是源文件。

    这个工具有两种输入，而原先"不给 `--lang`"一律走 JSON 那一支 —— 于是
    `lomt_from.py g.c` 把一份 C 源码喂给 `json.loads`，端到用户面前的是
    **Python 的 json 异常文本**（`Expecting value: line 1 column 1 (char 0)`），
    那句话对一份 C 文件没有任何意义；而**同一条流水的上一段**（`potato_from.py g.c`）
    却好好出来（rc=0）。`.c` 后缀本来就有确定答案，而 `resolve_lang` 这台工具本来就有
    （给了 `--lang auto` 时走的就是它）。

    两面都钉：源码文件不给 `--lang` 能过（且打得出 `[detect]`），
    而 `.potato.json` **照旧**当对象读（不许被 `resolve_lang` 拿去嗅）。
    """
    with tempfile.TemporaryDirectory() as t:
        td = Path(t)
        src = td / "g.c"
        src.write_text("int f(int x) { return x + 1; }\n", encoding="utf-8", newline="\n")
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "lomt_from.py"), str(src)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", shell=False, timeout=120)
        assert r.returncode == 0, f"rc={r.returncode} {(r.stdout + r.stderr)[:200]}"
        assert "[detect]" in r.stderr and "-> c" in r.stderr, r.stderr[:200]
        assert "Expecting value" not in r.stderr, f"又按 JSON 读了:\n{r.stderr[:200]}"
        # 形式对象那条路照旧（喂一份**合法**对象）
        obj = td / "o.potato.json"
        obj.write_text(json.dumps(_base(functions=[_fn(name="f")])), encoding="utf-8")
        r2 = subprocess.run([sys.executable, str(ROOT / "tools" / "lomt_from.py"), str(obj)],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", shell=False, timeout=120)
        assert r2.returncode == 0, f"rc={r2.returncode} {(r2.stdout + r2.stderr)[:200]}"
    print("      源码文件不给 --lang 会按后缀认；`.potato.json` 照旧当对象读")


def main() -> int:
    return loment_suite.run(TESTS, "loment_lomtfrom_test")


if __name__ == "__main__":
    sys.exit(main())
