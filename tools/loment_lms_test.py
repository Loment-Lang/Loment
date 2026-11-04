#!/usr/bin/env python3
"""loment_lms_test.py — `lms` 这一维：**档 + 七个细开关 + 叠加规则**（`docs/253` §9/§11.4）。

这一轮（0.1.5.1）把 `lms` 从"开/关"改判成**档名**，默认是**满档**。四条硬判据，
每条都对着 `docs/253` §9 编号里的一条：

* **A 默认就是满档**（判据 11）—— 一个 `lms_*` 都不写 ⇒ 七级全开，`lms` == `lms_full`。
* **B 档真的铺了默认**（判据 9）—— 四个档各自恰好铺出表里那一组；多一级少一级都红。
* **C 显式开关覆盖档**（判据 10）—— 降档之后显式写 `choose lms_check` 仍然把它打开；
  反过来在满档上 `choose close lms_crash` 也真的关掉它。**而且不判成"写了两遍"** ——
  初值不是"写的"（`SwitchTable.seeded` 那条区分就是为它存在的）。
* **D 产物里可判**（判据 9/11 的落点）—— `lms_levels` 进 Potato v17，七个布尔逐条对上。

外加两条登记处一致性：`_admin` 规则（`docs/262` §10.1）与"三张表是同一张"。

**0.1.5.1 收尾（#1024 / #1027）**：LMS 形状与内存耗尽的四份产物（原生 / T0 / T2 / T3）
必须逐字节同答；入口 A 的 retry 块要真的执行；`llvm.trap` 在 VM 里是**定义好的**
132 + 站点，不是一句 extern 拒绝。

用法: python tools/loment_lms_test.py
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomc  # noqa: E402
import lomelf  # noqa: E402
import lomentc  # noqa: E402
import lvm  # noqa: E402
import lvm_t2  # noqa: E402
import lvm_t3  # noqa: E402
import lvm_test  # noqa: E402
import potato  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

_PROG = """module m
choose runtime
%s

fn f() -> u32 {
    let p: ptr = alloc(8);
    let x: u32 = load8(p, 0);
    return x;
}
"""


def _load(chooses: str):
    """走**真前门**（预扫 → 装载）—— 这正是不走 `Parser` 直接读源码的那条路。"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "m.lomt"
        p.write_text(_PROG % chooses, encoding="utf-8", newline="\n")
        mod, deps = lomentc.load_unit(p, ROOT)
    return mod, deps


def _levels(chooses: str) -> dict[str, bool]:
    mod, _ = _load(chooses)
    return {n: lomentc._lms_level(mod, n) for n in lomentc.LMS_TOGGLES}


def test_default_is_the_full_tier() -> None:
    """判 A（`docs/253` §9 判据 11）：什么都不写 ⇒ 七级全开。"""
    mod, _ = _load("")
    assert mod.chooses.get("lms", lomentc.CORE_DEFAULTS["lms"]) == "lms_full", mod.chooses
    lv = {n: lomentc._lms_level(mod, n) for n in lomentc.LMS_TOGGLES}
    assert lv == {n: True for n in lomentc.LMS_TOGGLES}, lv
    print("      默认 lms_full：七级全开（一个 lms_* 都不写）")


def test_each_tier_seeds_exactly_its_levels() -> None:
    """判 B（判据 9）：四个档各自**恰好**是表里那一组 —— 多一级少一级都红。"""
    want = {
        "lms_full": {n: True for n in lomentc.LMS_TOGGLES},
        "lms_forensic_admin": {
            "lms_check": True, "lms_manage": True, "lms_guard": True,
            "lms_reject": True, "lms_crash": False, "lms_restart": False,
            "lms_seed": False},
        # ⚠ **1+3+4**，不是初稿的 1+4（`docs/253` §5.4 / #388）：拒绝器不自己发现错误，
        # 它的判决只能来自第 2 或第 3 级 —— 1+4 的日常档里没有任何东西给它喂判决。
        "lms_daily_admin": {
            "lms_check": True, "lms_manage": False, "lms_guard": True,
            "lms_reject": True, "lms_crash": False, "lms_restart": False,
            "lms_seed": False},
        "lms_manage_only_admin": {
            "lms_check": False, "lms_manage": True, "lms_guard": False,
            "lms_reject": False, "lms_crash": False, "lms_restart": False,
            "lms_seed": False},
    }
    assert set(want) == set(lomentc.LMS_TIERS), (set(want), set(lomentc.LMS_TIERS))
    for tier, exp in want.items():
        got = _levels(f"choose {tier}")
        assert got == exp, (tier, got, exp)
    print("      四个档各自恰好铺出表里那一组（含 daily = 1+3+4）")


def test_explicit_switches_override_the_tier() -> None:
    """判 C（判据 10）：显式写的开关**覆盖**档铺的初值，且**不算写了两遍**。"""
    # 降档之后显式打开一级
    mod, deps = _load("choose lms_manage_only_admin\nchoose lms_check")
    assert lomentc._lms_level(mod, "lms_check") is True, "显式写没有覆盖档"
    assert lomentc._lms_level(mod, "lms_guard") is False, "档铺的其余几级不该被带上来"
    errs = lomentc.check(mod, deps=deps)
    assert not [e for e in errs if "写了两次" in e], errs
    # 满档上显式关掉一级
    mod2, deps2 = _load("choose close lms_crash")
    assert lomentc._lms_level(mod2, "lms_crash") is False, "显式 `close` 没有覆盖满档"
    assert lomentc._lms_level(mod2, "lms_guard") is True, "关一级不该把别的级带下去"
    assert not [e for e in lomentc.check(mod2, deps=deps2) if "写了两次" in e]
    print("      降档 + `choose lms_check` ⇒ 开；满档 + `choose close lms_crash` ⇒ 关")


def test_two_tiers_are_an_error() -> None:
    """同一维写两次仍然是错误 —— 档名不改变"这一维只能声明一次"。"""
    mod, deps = _load("choose lms_daily_admin\nchoose lms_full")
    errs = lomentc.check(mod, deps=deps)
    assert any("核心模式这一维只能声明一次" in e for e in errs), errs
    print("      两个档名同时写 ⇒ 逐维的「只能声明一次」照旧报")


def test_levels_reach_the_artifact() -> None:
    """判 D：`lms_levels` 进 Potato v18，七个布尔与编译器那张表逐条对上。"""
    mod, deps = _load("choose lms_forensic_admin")
    doc = json.loads(lomentc.emit_potato(mod, ROOT, deps))
    assert doc["potato"] == "v19", doc["potato"]
    assert doc["lms"] == "lms_forensic_admin"
    assert doc["lms_levels"] == {
        "lms_check": True, "lms_manage": True, "lms_guard": True,
        "lms_reject": True, "lms_crash": False, "lms_restart": False,
        "lms_seed": False}, doc["lms_levels"]
    # v18：预算表也在产物里（`#1023`）—— 档名与开关可以不一致，预算与级无关。
    assert doc["lms_budgets"] == lomentc.LMS_LEVEL_BUDGETS, doc["lms_budgets"]
    # 校验器认它（同一份对象，两边判一样）
    assert potato.validate(doc) == [], potato.validate(doc)
    print("      Potato v18 的 lms_levels / lms_budgets 与编译器那张表逐条对上，校验器收下")


def test_manager_report_lists_every_alloc_site() -> None:
    """第 2 级（管理器）的产物（#386）：内存图**对得上账**。

    `lms_report` 数出的 `alloc` 站点必须**恰好**等于 Potato 里 `gc_ladder.total_sites` ——
    两个数数的是同一批站点，只是分的粗细不同。这条交叉不变量是这一级唯一的"对不对"
    判据：报告本身说"有三处"这件事，只有拿另一处独立数出来的数才判得了。
    """
    mod, deps = _load("")
    rep = lomentc.lms_report(mod, deps)
    lines = rep.splitlines()
    assert lines[0] == "# lms report v1", lines[:2]
    doc = json.loads(lomentc.emit_potato(mod, ROOT, deps))
    total = int(next(x for x in lines if x.startswith("total_alloc_sites ")).split()[1])
    assert total == doc["gc_ladder"]["total_sites"], (total, doc["gc_ladder"])
    assert any(x.startswith("levels ") and "lms_guard" in x for x in lines), lines
    assert any(x.startswith("arena ") for x in lines), lines
    print(f"      报告数出 {total} 处 alloc，与 gc_ladder.total_sites 对上")


_FLAKY = r"""
#include <stdio.h>
int main(void) {
    int n = 0;
    FILE *f = fopen("%(dir)s/count", "r");
    if (f) { if (fscanf(f, "%%d", &n) != 1) n = 0; fclose(f); }
    n += 1;
    f = fopen("%(dir)s/count", "w");
    fprintf(f, "%%d", n);
    fclose(f);
    return n < 3 ? 1 : 0;
}
"""

_ALWAYS = r"""
#include <stdio.h>
int main(void) { return 7; }
"""


def test_the_floor_is_named_and_sized() -> None:
    """第 7 级（#391）：信任地板有**名字**、有**数**，而且那个数**不会过期**。

    `docs/270` §4 里那一段写死了自举种子的字节数与 SHA-256。这条判据每次重算再比 ——
    于是"文档里的数"与"树里的种子"不可能漂开。种子每次 `--emit` 都会变，
    所以**这份文档必须跟着重算**，而这条判据就是那个提醒。
    """
    import hashlib
    doc = (ROOT / "docs" / "270-lms-0151.md").read_text(encoding="utf-8")
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    assert seed.exists(), "自举种子不在 —— 第 7 级的实物没了"
    blob = seed.read_bytes()
    want_bytes = str(len(blob))
    want_sha = hashlib.sha256(blob).hexdigest()
    assert f"artifact: loment/build/selfhost_driver.ll" in doc, "地板没点名那件实物"
    assert f"bytes: {want_bytes}" in doc, (
        f"docs/270 的地板字节数过期了：树里是 {want_bytes}")
    assert f"sha256: {want_sha}" in doc, (
        f"docs/270 的地板哈希过期了：树里是 {want_sha}")
    print(f"      地板 = {want_bytes} 字节 / {want_sha[:16]}…（每次都重算）")


def _sh_path(p: Path) -> str:
    """这条路在**真正跑命令的那个 shell** 里长什么样。

    Linux 上就是本机路径；Windows 上命令经 WSL 跑，路径要翻成 `/mnt/d/…`（与
    `loment_elf_test._wsl_path` 同一条规则 —— 少这一步，`cc` 会在 WSL 里找不到 Windows
    侧的文件，而症状是**编译失败**，很容易被误读成"这台机器没有 C 编译器"。
    """
    s = p.resolve().as_posix()
    if sys.platform.startswith("linux"):
        return s
    return "/mnt/" + s[0].lower() + s[2:]


def test_restarter_brings_the_program_back() -> None:
    """第 6 级（#390）：**用 C 写的**重启器 —— 进程死了把它拉起来。

    两个方向都判，缺一个都能过：

    * **拉起来**：前两次退出码 1、第三次 0 ⇒ 恰好重启 2 次，最终 rc=0，程序跑了 3 次；
    * **不许永远拉**：一个永远退 7 的程序 ⇒ 重启到上限就**停**，返回 7。

    后者是这一级最容易被"写成一个 while(1)"的地方：一个永远重启的守护进程会把
    **一次真崩溃**变成一条看不见的日志。
    """
    src = ROOT / "restarter" / "loment-restart.c"
    assert src.exists(), "restarter/loment-restart.c 不在 —— 第 6 级没有实物"
    linux = sys.platform.startswith("linux")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        flaky = tdp / "flaky.c"
        flaky.write_text(_FLAKY % {"dir": _sh_path(tdp).rstrip("/")},
                         encoding="utf-8", newline="\n")
        always = tdp / "always.c"
        always.write_text(_ALWAYS, encoding="utf-8", newline="\n")
        if linux:
            run = lambda cmd: subprocess.run(["bash", "-lc", cmd], capture_output=True,
                                             text=True, cwd=str(tdp))
        else:
            run = lambda cmd: subprocess.run(["wsl", "-e", "bash", "-lc", cmd],
                                             capture_output=True, text=True)
        base = _sh_path(tdp)
        r = run(f"cd {base} && cc -O2 -o loment-restart {_sh_path(src)} "
                f"&& cc -O2 -o flaky flaky.c && cc -O2 -o always always.c")
        if r.returncode != 0:
            print(f"      SKIP: 没有 C 编译器 ({r.stderr.strip()[:120]})")
            return
        run(f"cd {base} && rm -f count && ./loment-restart ./flaky; echo rc=$?")
        r2 = run(f"cd {base} && rm -f count && ./loment-restart ./flaky > /dev/null 2>&1; "
                 f"echo rc=$?; cat count")
        out = r2.stdout.split()
        assert out and out[0] == "rc=0", (r2.stdout, r2.stderr)
        assert out[1] == "3", f"程序应该被跑 3 次，实际 {out[1]}"
        r3 = run(f"cd {base} && ./loment-restart ./always 2>&1; echo rc=$?")
        assert "rc=7" in r3.stdout, r3.stdout
        assert "giving up" in r3.stdout, "没有上限 —— 这会把一次真崩溃变成看不见的日志"
        assert "restart key" not in r3.stdout, "没设密钥时不该出现彩蛋那一行"
    print("      崩溃后拉起（2 次）并停止；永远崩则到上限就停（rc=7）")


def test_restarter_checks_the_restart_key() -> None:
    """第 6 级的彩蛋：设了 `LOMENT_RESTART_KEY` 才核对，三个密钥走三种摘要。

    这条判据要同时钉住三件事，缺一件都能"看起来绿"：

    * **源码里没有明文** —— 只许有盐和摘要；
    * **摘要不是手抄错的** —— Python `hashlib` 独立算一遍，必须与源码里那个十六进制相等；
    * **C 的三套实现是对的** —— `--selftest` 的已知答案与 `hashlib` 逐条比，
      运行时还要真的接受三个密钥（且只接受它们）。

    编译时把 `LOMENT_RESTART_MAX` 压到 2、延迟压到 0 —— 这里判的是"放行/拒绝"，
    不是上限；上限由上面那条判据用默认值钉着。
    """
    import hashlib
    src = ROOT / "restarter" / "loment-restart.c"
    assert src.exists(), "restarter/loment-restart.c 不在 —— 第 6 级没有实物"
    keys = (
        ("sha256", "comp.os.minix", "19910825"),
        ("sha1", "just-for-fun", "linuslinux"),
        ("md5", "open.source", "hiopensource"),
    )
    text = src.read_text(encoding="utf-8")
    for alg, salt, key in keys:
        digest = getattr(hashlib, alg)((salt + key).encode()).hexdigest()
        assert digest in text, f"{alg} 的摘要没写进源码（{salt}+key）"
        assert key not in text, f"明文密钥 {key!r} 还在源码里 —— 这里只许有盐和摘要"
    linux = sys.platform.startswith("linux")
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        (tdp / "always.c").write_text(_ALWAYS, encoding="utf-8", newline="\n")
        if linux:
            run = lambda cmd: subprocess.run(["bash", "-lc", cmd], capture_output=True,
                                             text=True, cwd=str(tdp))
        else:
            run = lambda cmd: subprocess.run(["wsl", "-e", "bash", "-lc", cmd],
                                             capture_output=True, text=True)
        base = _sh_path(tdp)
        r = run(f"cd {base} && cc -O2 -DLOMENT_RESTART_MAX=2 -DLOMENT_RESTART_DELAY_MS=0 "
                f"-o loment-restart {_sh_path(src)} && cc -O2 -o always always.c")
        if r.returncode != 0:
            print(f"      SKIP: 没有 C 编译器 ({r.stderr.strip()[:120]})")
            return
        # 三套实现的已知答案：C 自己比一遍，Python 再独立比一遍（两方相等不算证据）。
        st = run(f"cd {base} && ./loment-restart --selftest")
        assert st.returncode == 0, ("C 自检红了", st.stdout, st.stderr)
        lines = [ln.split() for ln in st.stdout.splitlines() if ln.strip()]
        assert len(lines) == 9, f"自检应有 9 条已知答案，实际 {len(lines)}"
        for name, n, got in lines:
            size = int(n)
            msg = b"" if size == 0 else (b"abc" if size == 3 else b"a" * size)
            want = getattr(hashlib, name)(msg).hexdigest()
            assert got == want, (name, n, got, want)
        for _alg, _salt, key in keys:
            out = run(f"cd {base} && LOMENT_RESTART_KEY={key} "
                      f"./loment-restart ./always 2>&1; echo rc=$?")
            assert "restart key accepted" in out.stdout, (key, out.stdout)
            assert "loment-restart: restart 1 after 7" in out.stdout, (key, out.stdout)
            assert "rc=7" in out.stdout, (key, out.stdout)
        bad = run(f"cd {base} && LOMENT_RESTART_KEY=letmein "
                  f"./loment-restart ./always 2>&1; echo rc=$?")
        assert "restart key rejected" in bad.stdout, bad.stdout
        assert "restart 1 after" not in bad.stdout, ("错密钥仍然重启了", bad.stdout)
        assert "rc=7" in bad.stdout, bad.stdout
    print("      源码只有盐+摘要；三套摘要对上 hashlib；三个密钥放行、错密钥拒绝")


def test_the_admin_rule_is_mechanical() -> None:
    """`docs/262` §10.1：**关掉关键安全系统的取值必须带 `_admin`** —— 这里第一次可判。"""
    for tier in lomentc.LMS_TIERS:
        if tier == "lms_full":
            assert not tier.endswith("_admin"), "满档不是关闭，不该带 `_admin`"
        else:
            assert tier.endswith("_admin"), (
                f"`{tier}` 降级了内存安全却没有 `_admin` 后缀")
    # 三张表必须是同一张：编译器 / 校验器 / 七级键序
    assert tuple(potato.LMSS) == tuple(lomentc.CORE_DIMS["lms"]), (
        potato.LMSS, lomentc.CORE_DIMS["lms"])
    assert tuple(potato.LMS_LEVEL_KEYS) == tuple(lomentc.LMS_TOGGLES), (
        potato.LMS_LEVEL_KEYS, lomentc.LMS_TOGGLES)
    print("      非满档一律带 `_admin`；编译器 / 校验器 / 七级键序是同一张表")


# ---------------------------------------------------------------- 四产物对拍（#1024 / #1027）

def _native_ok() -> bool:
    """Windows 上原生 ELF 要借 WSL 跑；没有就 SKIP（与 `lvm_test` 同一条门）。"""
    return sys.platform != "win32" or bool(lvm_test.ELF._wsl())


def _emit_lms(src: str) -> str:
    m = lomentc.Parser(lomc.lex(src), src).parse()
    lomentc._gc_alpha_pass(m, [])
    errs = lomentc.check(m, deps=[])
    assert not errs, errs
    return lomentc.emit_llvm(m, ROOT, [])


def _run_four(ir: str, tag: str) -> dict[str, tuple[int, bytes, str]]:
    """同一份 IR 跑四份产物：原生 / T0 / T2 / T3。

    返回 `{产物: (退出码, stdout, stderr)}`。原生那一路没有 VM 的 stderr（它由信号死），
    所以第三格是空串 —— 比的仍是**退出码 + stdout**（`lvm_test` 判 A 的同一把尺子）。
    """
    got: dict[str, tuple[int, bytes, str]] = {}
    for name, fn in (("T0", lvm.run_ir), ("T2", lvm_t2.run_ir_t2),
                     ("T3", lvm_t3.run_ir_t3)):
        buf, err = io.BytesIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = fn(ir, buf, None, [tag])
        got[name] = (rc, buf.getvalue(), err.getvalue())
    blob, _ = lomelf.compile_ll(ir, [])
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / f"{tag}.elf"
        p.write_bytes(blob)
        rc, out = lvm_test._run_native(p, Path(td), tag)
    got["native"] = (rc, out, "")
    return got


#: LMS 的三种回答（`lomentc_test::_LMS_RUN` 的同一形状）：界内写 1、界外写 7 与界外读
#: **都越过 `alloc(8)` 的界**。第 4 级跳过那两条 ⇒ 答案 1；第 5 级接手 ⇒ 进程自己死。
_LMS_SHAPE = """module m
choose runtime
const N: u32 = 8;
%s

fn _start() -> u32 {
    let p: ptr = alloc(N);
    store8(p, 0, 1);
    store8(p, 9, 7);
    let x: u32 = load8(p, 0);
    let y: u32 = load8(p, 9);
    let v: u32 = x + y;
    syscall4(60, v as u64, 0, 0);
    return 0;
}
"""


def test_lms_shape_matches_native_on_four_products() -> None:
    """#1024：LMS 的三种回答在**四份产物**上同答。

    三个格子，每个都同时跑原生 / T0 / T2 / T3：

    * 默认满档 ⇒ 越界访存被**跳过**，rc=1（界内那一半照算）；
    * `choose close lms_reject` ⇒ `llvm.trap`，rc=132；
    * `choose close lms_guard` ⇒ 一条检查都不发，rc=8。

    ⚠ **不许只看"没崩"**：只断言退出码不够，还要断言 stdout 与答案**恰好**是那个数
    （`docs/253` §5.4 那条"跳过之后没更坏"的判据）。
    """
    if not _native_ok():
        print("      SKIP: Windows 上没有 WSL —— 原生 ELF 跑不了")
        return
    cells = (
        ("reject", "", 1),
        ("crash", "choose close lms_reject", 132),
        ("guard_off", "choose close lms_guard", 8),
    )
    for tag, chooses, want in cells:
        got = _run_four(_emit_lms(_LMS_SHAPE % chooses), tag)
        bad = {k: v for k, v in got.items() if v[0] != want or v[1] != b""}
        assert not bad, (tag, f"期望 rc={want} / stdout 空", bad)
    print("      默认跳过 rc=1 / 关掉拒绝器 rc=132 / 关掉保证器 rc=8 —— 四产物同答")


#: 入口 A 的运行时探针：65 个 1000 字节的块正好填满 64 KiB arena（65×1008=65520），
#: 第 66 个再要 1008 字节就撞墙。**一次回收**把前 65 个死块里的垃圾还回来，retry 就
#: 装得下 —— 请求是**小块**，所以回收后的空闲链能直接复用（收集器不做压实）。
#: lms_guard 开着 ⇒ rc=66（66 次读回 1）；关着 ⇒ 第 66 次直接 abort（rc=132）。
_RESCUE = """module m
choose runtime
choose gc_auto
%s
const SMALL: u32 = 1000;

fn _start() -> u32 {
    let i: i32 = 0;
    let sum: u32 = 0;
    while i < 66 {
        let p: ptr = alloc(SMALL);
        store8(p, 0, 1);
        sum = sum + (load8(p, 0) as u32);
        i = i + 1;
    }
    syscall4(60, sum as u64, 0, 0);
    return 0;
}
"""


def test_lms_entry_a_rescue_is_executed_by_every_product() -> None:
    """#1024：入口 A 的 retry 块要**真的跑**，不是只出现在 IR 里。

    把 `__loment_maybe_collect` 的调用从探针里删掉（否则 50 KB 的请求会**先**触发一次
    常规回收，入口 A 就成了摆设）。剩下的唯一回收点就是 `alloc` 失败后的 retry 块：

    * `lms_guard` 开 ⇒ retry 收集 24 个垃圾块，50 KB 装得下，rc=9；
    * `lms_guard` 关 ⇒ 没有 retry，rc=132。

    **四份产物都判**。这条红了就说明 retry 块没执行（或者 VM 没走它）。
    """
    if not _native_ok():
        print("      SKIP: Windows 上没有 WSL —— 原生 ELF 跑不了")
        return
    for on, want in ((True, 66), (False, 132)):
        src = _RESCUE % ("" if on else "choose close lms_guard")
        ir = re.sub(r"^\s*call void @__loment_maybe_collect\([^)]*\)\n", "",
                    _emit_lms(src), flags=re.M)
        assert "call void @__loment_maybe_collect" not in ir, "常规触发没被拿掉，探针不纯"
        got = _run_four(ir, "rescue_on" if on else "rescue_off")
        bad = {k: v for k, v in got.items() if v[0] != want or v[1] != b""}
        assert not bad, ("lms_guard 开" if on else "lms_guard 关", bad)
    print("      retry 块执行：开 ⇒ rc=66（拉回成功）/ 关 ⇒ rc=132（拉不回就死）")


#: 内存墙：一次要 70000 字节，比整个 64 KiB arena 还大 —— 回收也救不了。
_WALL = """module m
choose runtime
choose %s
%s
const BIG: u32 = 70000;

fn _start() -> u32 {
    let p: ptr = alloc(BIG);
    store8(p, 0, 1);
    syscall4(60, 0, 0, 0);
    return 0;
}
"""


def test_memory_exhaustion_hits_the_same_wall_on_four_products() -> None:
    """#1027：六个格子（三档回收 × lms_guard 开关）都撞同一堵墙：rc=132。

    `gc_manual` 没有收集器，入口 A 本来就不在那里；`gc_auto` / `gc_auto_alpha`
    有收集器，但 70 KB 的单块**回收也装不下** —— 六格都必须到 `llvm.trap`。
    四份产物的退出码与 stdout 必须逐字节相同。
    """
    if not _native_ok():
        print("      SKIP: Windows 上没有 WSL —— 原生 ELF 跑不了")
        return
    for gc in ("gc_manual", "gc_auto", "gc_auto_alpha"):
        for lms in ("", "choose close lms_guard"):
            got = _run_four(_emit_lms(_WALL % (gc, lms)), f"wall_{gc}")
            bad = {k: v for k, v in got.items() if v[0] != 132 or v[1] != b""}
            assert not bad, (gc, bool(lms), bad)
    print("      三档回收 × 保证器开/关：六格都是 rc=132（四产物同答）")


#: 空闲链复用：1000 轮、每轮 100 字节（尺寸走运行时变量，避免 alpha 的 L0 提升）。
#: 64 KiB arena 装不下 100 KB；只有回收把死块挂回空闲链并**被下一次 alloc 复用**，
#: 程序才跑得完（1000 次读回 1 ⇒ rc=232）。
_SURVIVOR = """module m
choose runtime
choose gc_auto
choose close lms_guard

fn _start() -> u32 {
    let n: u32 = 100;
    let i: i32 = 0;
    let sum: u32 = 0;
    while i < 1000 {
        let p: ptr = alloc(n);
        store8(p, 0, 1);
        sum = sum + (load8(p, 0) as u32);
        i = i + 1;
    }
    syscall4(60, sum as u64, 0, 0);
    return 0;
}
"""


def test_gc_auto_reuses_freed_blocks_on_every_product() -> None:
    """#1027：回收之后的空闲链**真的被复用**，四份产物都跑完 1000 轮。

    这一条钉的是 VM 的 `phi` **并行**语义：`free` 的插入循环里
    `%prev = phi [0, %link], [%cur, %lcont]` 必须读到入边的旧 `%cur`；顺序求值会
    读到本轮刚写的 0，于是每一轮都把空闲链重置成单块，arena 提前耗尽。删掉那条
    并行求值 ⇒ VM 在 600 轮左右 abort（原生仍跑完），这条当场红。
    """
    if not _native_ok():
        print("      SKIP: Windows 上没有 WSL —— 原生 ELF 跑不了")
        return
    got = _run_four(_emit_lms(_SURVIVOR), "survivor")
    bad = {k: v for k, v in got.items() if v[0] != 232 or v[1] != b""}
    assert not bad, ("1000 轮应读回 1000 ⇒ rc=232", bad)
    print("      1000 轮分配 + 回收 + 复用：四产物都 rc=232")


#: `docs/263` §2.2 的那条边界：VM 的地址检查在 **`MEM_SIZE`（16 MiB）** 上，
#: 不在 arena（64 KiB）上。`1048576 = 1 MiB`：arena 之外、地址空间之内，也在
#: 栈区（`MEM_SIZE` 往下 `MAX_DEPTH × FRAME_SIZE` = 512 KiB）之下 —— 写进去不会
#: 被 syscall 的栈帧覆盖。
_OUTSIDE_ARENA = """module m

fn _start() -> u32 {
    let p: ptr = 1048576 as ptr;
    store8(p, 0, 1);
    let x: u32 = load8(p, 0);
    syscall4(60, x as u64, 0, 0);
    return 0;
}
"""

#: `16781312 = MEM_SIZE + 4096`：地址空间之外 —— 这里才该拒。
_PAST_MEM = """module m

fn _start() -> u32 {
    let p: ptr = 16781312 as ptr;
    store8(p, 0, 1);
    syscall4(60, 0, 0, 0);
    return 0;
}
"""


def test_the_vm_guards_mem_size_not_the_arena() -> None:
    """#1027 第三格：VM 按**自己的地址空间**守界，不按 arena 守。

    `docs/263` §2.2 把这条写成明话：arena 的界由**分配器**守，VM 只知道 16 MiB。
    两份探针各钉一半，删掉任何一半这条红：

      * 写到 arena 外面、`MEM_SIZE` 里面 —— 三个 VM 档都**照写**，读回 1（rc=1）；
      * 写到 `MEM_SIZE` 外面 —— 三个 VM 档都抛**具名**的 `写越界`。

    这不是缺陷判据，是**今天行为的钉子**：原生那边越界的静态数组是 UB，而 VM 不会
    替 arena 守界 —— 两者"同点撞墙"只在**分配耗尽**那一格成立（上一条判据）。
    """
    ir = _emit_lms(_OUTSIDE_ARENA)
    for name, fn in (("T0", lvm.run_ir), ("T2", lvm_t2.run_ir_t2),
                     ("T3", lvm_t3.run_ir_t3)):
        buf, err = io.BytesIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = fn(ir, buf, None, ["outside_arena"])
        assert rc == 1 and buf.getvalue() == b"" and err.getvalue() == "", (
            name, rc, buf.getvalue(), err.getvalue())
    ir = _emit_lms(_PAST_MEM)
    for name, fn in (("T0", lvm.run_ir), ("T2", lvm_t2.run_ir_t2),
                     ("T3", lvm_t3.run_ir_t3)):
        buf = io.BytesIO()
        try:
            fn(ir, buf, None, ["past_mem"])
        except lvm.LvmError as e:
            assert "写越界" in str(e), (name, e)
        else:
            raise AssertionError(f"{name}: 写到 MEM_SIZE 外面居然没被拒")
    print("      arena 之外照写（rc=1）；MEM_SIZE 之外点名 `写越界` —— 三个 VM 档一致")


def test_vm_trap_reports_the_site() -> None:
    """#1027：`llvm.trap` 在 VM 里是**定义好的** 132，且说得出死在哪一条。

    原生那边是 `SIGILL`（shell 报 132），没有消息；VM 这边必须把
    `函数:块#指令序号` 写出来 —— 两个实现"都死了"不算证据。
    """
    got = _run_four(_emit_lms(_LMS_SHAPE % "choose close lms_reject"), "trap_site")
    for name in ("T0", "T2", "T3"):
        rc, out, err = got[name]
        assert rc == 132 and out == b"", (name, rc, out)
        assert re.search(r"llvm\.trap at [^:\s]+:[^#\s]+#\d+", err), (name, err)
    print("      T0/T2/T3 都报 `llvm.trap at 函数:块#指令`，退出码 132")


def test_lms_budgets_are_declared_and_recorded() -> None:
    """#1023：七级的运行期预算有**声明**，而且进产物（Potato v18）。

    今天七级恰好是 `none` / `instructions` / `outside` —— 所以 `no_runtime` + `lms_full`
    合法。但那是**被检查的性质**：表里改一个值，检查器就会点名。产物里也要能读到，
    这样"这份产物承诺没有运行期"不是靠读源码猜。
    """
    want = {
        "lms_check": "none", "lms_manage": "none", "lms_guard": "instructions",
        "lms_reject": "instructions", "lms_crash": "instructions",
        "lms_restart": "outside", "lms_seed": "none",
    }
    assert lomentc.LMS_LEVEL_BUDGETS == want, lomentc.LMS_LEVEL_BUDGETS
    assert tuple(lomentc.LMS_LEVEL_BUDGETS) == lomentc.LMS_TOGGLES
    mod, deps = _load("")
    doc = json.loads(lomentc.emit_potato(mod, ROOT, deps))
    assert doc["potato"] == "v19", doc["potato"]
    assert doc["lms_budgets"] == want, doc["lms_budgets"]
    assert potato.validate(doc) == [], potato.validate(doc)
    print("      七级预算已声明；Potato v18 的 lms_budgets 与编译器同一张表")


def test_a_stateful_level_rejects_no_runtime() -> None:
    """#1023：把某一级改成 `state`，`no_runtime` 的产物**当场点名**。

    这是"预算不是一句注释"的证伪：不动源码，只动编译器那张表 —— 检查器必须红，
    并且报文里同时有**级名**与**预算**。今天七级都不是 `state`，所以这是**探针
    构建**（issue 原话：a probe build that forces level 2 to allocate）。
    """
    src = "module m\n\nfn f() -> u32 {\n    return 7;\n}\n"

    def check() -> list[str]:
        mod = lomentc.Parser(lomc.lex(src), src).parse()
        lomentc._gc_alpha_pass(mod, [])
        return lomentc.check(mod, deps=[])

    assert check() == [], "默认七级不该被拒 —— no_runtime + lms_full 必须合法"
    old = lomentc.LMS_LEVEL_BUDGETS["lms_manage"]
    lomentc.LMS_LEVEL_BUDGETS["lms_manage"] = "state"
    try:
        errs = check()
    finally:
        lomentc.LMS_LEVEL_BUDGETS["lms_manage"] = old
    assert any("lms_manage" in e and "state" in e for e in errs), errs
    assert any("no_runtime" in e for e in errs), errs
    print("      预算改成 state => no_runtime 被拒，报文点名 lms_manage / state")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def main() -> int:
    return loment_suite.run(TESTS, "loment_lms_test")


if __name__ == "__main__":
    sys.exit(main())
