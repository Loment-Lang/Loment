#!/usr/bin/env python3
"""渠道判据（`docs/231` §2、§5 判据 D 与 F）。

**这一条为什么必须存在**：渠道（`LOMENT_CHANNEL`）不是一个命令，是**这条流水线**
（`loment_dist.py`）—— 它把自举侧那一行常量翻成 `1` 再建包。**没有判据的话，那一步
悄悄失效谁也不知道**。它当初守的是"发行版拒 `choose vm`"（用户 2026-10-11 定）；
**2026-10-20 起五道闸全放开**（最后一道 `lspe` 随 0.1.5 落地），于是它现在守的是
"注入仍然生效、而且只生效在那一处" —— 五份探针在两端都必须被**收**。

两半：

* **F（轻，到处可跑）**：发行版种子 = f(仓树, channel=release)，**可重算**；
  而且它与 dev 种子的差**恰好是那一个常量的效果** —— 差**一个字节**，长度相同。
  这条是放闸之后**真正的守门人**：闸没了，注入要是也悄悄没了，只有它会红。
* **D（重，要 clang + WSL）**：真建一对 dev / 发行版驱动器，五份探针都必须过。
  **两端都要**：只测"发行版收"的话，一个把发行版当 dev 的构建也能绿。

用法: python tools/loment_channel_test.py     (退出码 0 = 全过)
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import loment_p8_test as P8  # noqa: E402
import loment_seed  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SEED = loment_seed.SEED

#: 探针：一份**声明了 `choose vm`** 的最小单元。**2026-10-17 起发行版也必须收它**
#: （用户裁决：LVM 完成一大半）。与 `use libl`、`choose hosted` 同一个棘轮做法：
#: 探针**留着**、期望**反转** —— 删掉等于把"这道闸曾经存在"一起删掉。
PROBE_VM = "module probe_vm\n\nchoose vm\n\nfn f() -> u32 {\n    return 1;\n}\n"
#: 对照组：同样一份、**不声明** `choose vm` —— 两个驱动器都必须收。
PROBE_PLAIN = "module probe_plain\n\nfn f() -> u32 {\n    return 1;\n}\n"
#: 探针：`choose hosted`（`docs/222` §4）。**2026-10-17 起发行版必须收它**（`#406` 开闸：
#: V1 静态 `.a`（`#359`）与 V2 运行期 `.so`（`#360`）两半都落齐了）。与 `use libl` 那条
#: 同一个棘轮做法：探针**留着**、期望**反转** —— 删掉等于把"这道闸曾经存在"一起删掉。
#: 入口写 `fn main`（`docs/222` §4.3：hosted 产物由 C 运行期起）—— 这一条只看驱动器收不收。
PROBE_HOSTED = "module probe_hosted\n\nchoose hosted\n\nfn main() -> u32 {\n    return 1;\n}\n"
#: 探针：一份 `use libl` 的最小单元。**2026-10-15 起发行版必须收它**（`#358` 放开闸，
#: `docs/232` §1.1）。这条探针**不是删掉而是反转** —— 棘轮规矩：期望改了就把判据反过来，
#: 删掉等于把"这道闸曾经存在"这件事一起删掉。放开的前提写在 `docs/232` §1.3 那份承诺里。
#:
#: `choose runtime` 不能省：`libl` 是门面，它拉的那几份都声明了 `choose runtime`（库里写
#: `choose` 是**需求**，根必须满足，`docs/227` §2.2）。少了它就先在运行期那关被拒，
#: 测到的就不是渠道闸了。
PROBE_LIBL = ("module probe_libl\n\nchoose runtime\n\nuse libl\n\n"
              "fn f() -> u32 {\n    return 1;\n}\n")
#: 探针：`choose lspe_open_admin`（`docs/262`）。**2026-10-20 起发行版必须收它** ——
#: LSPE 第一部分（维度 + 台账 + 普查）已随 0.1.5 落地，最后一道渠道闸打开。
#: 这是**反转过的老闸**：0.1.4.7 起它要求"发行版拒"，探针留着不删（棘轮）。
PROBE_LSPE = ("module probe_lspe\n\nchoose lspe_open_admin\n\n"
              "fn f() -> u32 {\n    return 1;\n}\n")

#: 渠道常量在源码里被**读到**的处数 —— 也就是 dev/release 两个种子该差几处。
#:
#: **今天仍是 1，但含义变了**（2026-10-20，最后一道 `lspe` 闸放开之后）：`checker.lomt` 的
#: `check()` 里那一个 `let rel` 是**唯一**的读取点，而**已经没有闸读它了** —— 它还留着，
#: 是因为渠道是"下一个还没兑现的维"的注入点，拆渠道是另一次改动。于是两个种子只差 1 个
#: 字符，这条判据守的东西从"闸生效"变成了"**注入仍然生效**"。
#:
#: 历史：LibL 的闸还在时这个数是 2；再往前（端口闸各写一个 `let` 那版）曾变成 3，
#: 判据当场红、而红的样子**像"注入改歪了"**。
#:
#: **改这个数之前先读那条判据的 docstring**：理由是"不为此往自举单元里加函数"。
#: ⚠ **新增一条闸时也别各写一个 `let`**：提到共用位置（同文件内）才对。
CHANNEL_READ_SITES = 1

TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


# ---------------------------------------------------------------- F：轻

@test
def test_repo_seed_is_dev_channel():
    """仓里那份种子是 **dev** —— 于是本仓的工具链接受 `choose vm`（`docs/231` §2.2）。"""
    got = loment_seed.reference_ir("dev")
    assert SEED.exists(), f"缺种子 {SEED}（跑 python tools/loment_seed.py --emit）"
    want = SEED.read_text(encoding="utf-8")
    assert got == want, ("仓里的种子与 dev 渠道的参考产物不一致 —— 先跑 "
                         "`python tools/loment_seed.py --emit`")


@test
def test_release_seed_differs_only_in_the_channel_constant():
    """发行版种子与 dev 种子的差**只有那个常量的取值**，而且**处数是可数的**。

    `docs/231` §2.3 承诺的是"包里**除那一行之外**逐字节等于仓库"（源码层：打包只替换
    `checker.lomt` 里的 `LOMENT_CHANNEL` 那一行）。这条判据把它的**产物层**后果钉成可证伪的：

      * 两个种子**长度相同**；
      * 差异**只在**那些"常量被读出来的地方"（IR 里是一条 `store i32 0/1, ptr %rel…`）；
      * 每一处都是 `'0' → '1'`；
      * **处数恰好等于** `CHANNEL_READ_SITES`。

    **处数是钉出来的**：`CHANNEL_READ_SITES` 今天写死 1（2026-10-15 `#358` 放开 `use libl`
    之后又回到 1 —— 见那个常量上面的注释）。以后多一处读取点 ⇒ 这条红，逼改的人
    **先想清楚**那是不是有意的（并顺手更新 `docs/231` §5 判据 F 的口径）。

    **为什么不去动读取点的布局**：`checker.lomt` 里那两个 `choose` 的闸之所以**共用同一个
    `let`** 而不是各写一个，是因为往自举单元里**加函数**很贵 —— `checker.lomt` 那条注释
    记着实测 A/B：加函数那版 **62.9s（红）**、纯净树 58.24s（过），60s 护栏只剩 ~1% 余量。
    **用一个新函数换回一个好看的数字，不值得。**
    """
    dev = loment_seed.reference_ir("dev")
    rel = loment_seed.reference_ir("release")
    assert len(dev) == len(rel), (
        f"两个种子长度不同（dev {len(dev)}B vs release {len(rel)}B）—— 注入改的"
        "**不止那个常量**（`docs/231` §2.3）")
    diff = [i for i in range(len(dev)) if dev[i] != rel[i]]
    assert diff, "两个种子一模一样 —— 渠道注入**根本没生效**（发行版会照收 `choose vm`）"
    assert len(diff) == CHANNEL_READ_SITES, (
        f"两个种子差 {len(diff)} 处（第 {diff[:5]} 个字符），而钉住的是 "
        f"{CHANNEL_READ_SITES} 处 —— 渠道常量被读到的**处数变了**。"
        f"多出来的那处要么是有意的（那就更新这个数与 `docs/231` §5 判据 F 的口径），"
        f"要么是注入改歪了")
    lines = dev.splitlines()
    for i in diff:
        assert (dev[i], rel[i]) == ("0", "1"), (
            f"第 {i} 个字符是 {dev[i]!r} → {rel[i]!r}，不是 '0' → '1'（`docs/231` §2.2）")
        # 每一处都必须落在"把常量存进那个局部变量"那一行上 —— 否则是"碰巧也只差这些处"。
        around = lines[dev[:i].count("\n")]
        assert "store i32 0, ptr %rel" in around, f"那一处在别的地方：{around!r}"


@test
def test_release_seed_is_deterministic():
    """重算两次得到同一串字节 —— 判据建立在"可重算"上（`docs/231` §5 判据 F）。"""
    a = loment_seed.reference_ir("release")
    b = loment_seed.reference_ir("release")
    assert a == b, "发行版种子两次算出来不一样 —— 判据没法建立在它上面"


# ---------------------------------------------------------------- D：重

@test
def test_dist_builds_with_the_release_seed():
    """`loment_dist.emit()` 走的是**发行版种子** —— 这是"包 = 发行版"的接线点。

    只钉**接线**（不建包）：把 `_use_release_seed()` 跑一遍，断言 `ACTIVE_SEED` 指到了
    `RELEASE_SEED` 那份、且那份的内容 == 现算的发行版种子。
    """
    import loment_dist  # noqa: PLC0415

    loment_dist._use_release_seed()
    assert loment_dist.ACTIVE_SEED == loment_dist.RELEASE_SEED, (
        "`emit()` 没有把打包用的种子换成发行版那份 —— 包里会是 dev 工具链，"
        "于是发行版照样收 `choose vm`（`docs/231` §2.3）")
    assert loment_dist.RELEASE_SEED.read_text(encoding="utf-8") == \
        loment_seed.reference_ir("release"), "落盘的发行版种子与现算的不一致"


@test
def test_the_python_checker_accepts_hosted_in_both_channels():
    """参考实现的**端口闸已放开**（`#406`，2026-10-17）：`choose hosted` 在两个渠道都收。

    **这条判据是反转过来的老闸**：它原来要求"发行版拒"。期望改了就把期望反过来 ——
    探针留着，这样"这道闸曾经存在、某天被打开"在判据里是有痕迹的。

    **不需要 clang** —— 这一条只走 `load` + `check`，所以它在哪儿都能跑。两端都测：
    只测一边的话，"什么都拒"或"什么都收"的检查器都能绿。
    """
    import lomentc  # noqa: PLC0415
    import loment_channel  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "h.lomt"
        p.write_text(PROBE_HOSTED, encoding="utf-8", newline="\n")
        mod = lomentc.load(p)
        deps = lomentc.resolve_deps(mod, ROOT, Path(td), entry=p)
        old = loment_channel.RELEASE
        try:
            loment_channel.RELEASE = False
            dev = lomentc.check(mod, deps=deps)
            loment_channel.RELEASE = True
            rel = lomentc.check(mod, deps=deps)
        finally:
            loment_channel.RELEASE = old
    assert not dev, f"开发通道拒了 `choose hosted` —— 那这一维就永远是死的：{dev}"
    assert not rel, f"发行版**拒**了 `choose hosted` —— 开闸（`#406`）没落地：{rel}"
    print("      Python 检查器：dev 收 / 发行版也收 `choose hosted` —— 两端都核过")


@test
def test_both_launchers_open_the_port_door():
    """启动器（`docs/222` §4）：**两个渠道都给链接器 `--allow-ports`**（`#406` 开闸）。

    **这条判据是反转过来的老闸**：它原来要求"发行版永不传那面旗、并把 `--hosted` 点名
    拒掉"。现在两半都落齐了，两个渠道必须**都传**；而且发行版**不许**再有那个点名拒的
    分支（留着的话，命令行 `--hosted -lz` 在包里仍然进不去）。

    链接器的默认仍然是"拒"（它不知道渠道）—— 所以这面旗必须**真的**在启动器里出现，
    而不是靠哪条默许。
    """
    import loment_dist  # noqa: PLC0415

    for name, tpl in (("launcher.sh", loment_dist.LAUNCHER_SH),
                      ("launcher.cmd", loment_dist.LAUNCHER_CMD)):
        dev = loment_dist._subst(tpl, "dev")
        rel = loment_dist._subst(tpl, "release")
        for label, text in (("dev", dev), ("release", rel)):
            assert "--allow-ports" in text, (
                f"{name}/{label}: 启动器没给链接器 `--allow-ports` —— 包里的 libc 就还是"
                "调不动（`#406`）")
        assert "release build" not in rel, (
            f"{name}: 发行版启动器里还留着 `--hosted` 的点名拒 —— 开闸没落地")
    assert "--hosted" in loment_dist.LAUNCHER_SH, "启动器必须留着 `--hosted`"
    print("      两份启动器：两个渠道都传 `--allow-ports`，发行版不再点名拒 `--hosted`")


@test
def test_release_driver_refuses_channel_gated_constructs():
    """**判据 D**：五份探针在两个驱动器里都过 —— 渠道闸已全部放开，两端都要测。

    机制同一条（读同一个渠道常量）。五道闸的历史：

    * **`choose vm`** —— 一维核心模式，闸在 `check()`（`docs/231` §2）。**2026-10-17 放开**
      （用户裁决，LVM 完成一大半）：探针留着、期望反转，发行版现在必须**收**它；
    * **`choose hosted`** —— 另一维核心模式。**2026-10-17 放开**（`#406`：V1 与 V2 两半
      都落齐了）：探针留着、期望反转，发行版现在必须**收**它；
    * **`use libl`** —— 一个**名字**，闸在**名字解析**处。**2026-10-15 放开**（`#358`）：
      探针**留着**、期望**反转**，发行版现在必须**收**它。棘轮规矩是**替换**不是删掉；
      放开的条件写在 `docs/232` §1.3 那份承诺里；
    * **`choose lspe_*`** —— 第七维核心模式。**2026-10-20 放开**（用户裁决，0.1.5 发布前）：
      LSPE 第一部分随 0.1.5 落地，探针**留着**、期望**反转**（`docs/262` §10.6）。

    五份探针都测，而且**共用同一对驱动器**（各建一次 ELF 很贵）—— 几道闸读的是同一个
    常量，建三对驱动器只是把同一件事再做一遍。

    跳过条件与 `loment_p8_test` 同：要 clang（建 ELF）与 WSL（跑 ELF）。
    """
    if not P8._clang() or not P8._wsl():
        print("      SKIP: 需要 clang + WSL")
        return
    probe_dir = ROOT / "loment" / "build"
    probe_dir.mkdir(parents=True, exist_ok=True)
    #: (标签, 源码, 发行版里必须拒吗) —— `plain` 那行是**对照组**：它在两边都得过；
    #: `libl` 那行是**反转过来的老闸**，也是"发行版收"的第二例。
    #:
    #: **没有"报文里必须点到的名字"这一栏了**：驱动器只吐**码 + 位置 + 肇事 token**、
    #: 文本由报错器渲染（`docs/182` §5.3），而剩下的两关肇事 token 都是 `choose` 这个
    #: 关键字、不是被闸关住的模式名 —— 它们报文的文本由 `loment_diag` 那张 E022 卡负责，
    #: 已由上面那条 Python 判据守着。原来那一栏只服务 `use libl` 那条点名闸，闸放开了。
    probes = [("plain", PROBE_PLAIN, False),
              ("vm", PROBE_VM, False),
              ("libl", PROBE_LIBL, False),
              ("hosted", PROBE_HOSTED, False),
              # 2026-10-20：最后一道闸也打开 —— LSPE 第一部分已随 0.1.5 落地。
              ("lspe", PROBE_LSPE, False)]
    made: list[Path] = []
    try:
        for tag, src, _refuse in probes:
            p = probe_dir / f"channel_probe_{tag}.lomt"
            p.write_text(src, encoding="utf-8", newline="\n")
            made.append(p)
        rel = "loment/build/channel_probe_%s.lomt"
        with tempfile.TemporaryDirectory() as td:
            # ELF 与产物都落在**这个** td 里 —— 上面的 `_build_linux_elf` 返回的是
            # td 里的路径，td 一销毁它就是悬空的（第一版踩过）。
            dev_elf = P8._build_linux_elf(SEED.read_text(encoding="utf-8"), td, "chan_dev")
            rel_elf = P8._build_linux_elf(loment_seed.reference_ir("release"), td, "chan_rel")
            for tag, _src, refuse in probes:
                # **dev 每一份都得过** —— 不然"发行版拒"可能只是因为它什么都拒。
                P8._run_driver(dev_elf, rel % tag, td, f"cd_{tag}")
                rc, _out, err = P8._run_driver_raw(rel_elf, rel % tag, td, f"cr_{tag}")
                if refuse:
                    assert rc != 0, (
                        f"发行版驱动器收下了 `{tag}` 那条闸 —— 渠道闸没生效"
                        f"（`docs/231` §2 / `docs/222` §4.5）")
                else:
                    assert rc == 0, f"对照组在发行版里被拒了：{err[-200:]}"
        print("      dev 都收；发行版五份都收（最后一道 lspe 闸 2026-10-20 放开）")
    finally:
        for p in made:
            p.unlink(missing_ok=True)


#: `choose hosted` + 运行期的最小产品：`alloc` 会把宿主的 `malloc` 拉成导入，`abs` 是我们
#: 自己要的那个名字。退出码 7 = `abs(-7)` —— 任何一个环节错都到不了这个数。
PROBE_RELEASE_LIBC = """module probe_release_libc
choose runtime
choose hosted

extern fn abs(v: i32) -> i32;

fn _start() {
    let p: ptr = alloc(8);
    store8(p, 0, 65 as u8);
    let v: i32 = abs(0 as i32 - 7);
    syscall4(60, v as u64, 0, 0);
}
"""


@test
def test_a_release_build_links_the_real_libc_end_to_end():
    """**开闸之后，发行版真的能链真 libc 并跑起来**（`#406` 第一条）。

    在这之前发行版里 `choose hosted` 是编译错误 —— 于是"能链真库"是开发通道的事实，
    不是用户拿得到的能力。这一格把两半接上：渠道常量翻成 `release` 之后

      * `check()` **收** `choose hosted`（编译器的门开了）；
      * 链接器拿着启动器会给的那面旗（`--allow-ports`），把 `libc.so.6` 链成动态产物；
      * 产物在 Linux 上跑出 7（`abs(-7)`），而 `malloc` 那条导入是**运行期**从 glibc 解析的。

    **另一半是"仍然关着"**（`#406` 第三条）：同一个产品里写一个谁都没有的名字
    （既不在收编面里、也不在链上的 `.so` 的导出表里）⇒ **链接期点名拒**。
    开闸开的是"真库可以进来"，不是"名字随便写"。
    """
    import loment_ffi_test as ffitest  # noqa: PLC0415
    import loment_channel  # noqa: PLC0415
    import lomelf  # noqa: PLC0415
    import lomentc  # noqa: PLC0415

    if not P8._wsl():
        print("      SKIP: 需要 WSL（或 Linux）跑 ELF")
        return
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        probe = tdp / "release_libc.lomt"
        probe.write_text(PROBE_RELEASE_LIBC, encoding="utf-8", newline="\n")
        libc = ffitest._system_so(tdp / "libc.so.6", ffitest._LIB6_CANDIDATES)
        if libc is None:
            print("      SKIP: 这台机器上没有 libc.so.6 —— 这一格测不了")
            return
        old = loment_channel.RELEASE
        try:
            loment_channel.RELEASE = True
            mod = lomentc.load(probe)
            deps = lomentc.resolve_deps(mod, ROOT, tdp, entry=probe)
            errs = lomentc.check(mod, deps=deps)
            assert not errs, f"发行版拒了 `choose hosted` —— 开闸没落地：{errs[:1]}"
            ir = lomentc.emit_llvm(mod, ROOT, deps)
            blob, info = lomelf.compile_ll(ir, [lomelf.load_foreign(libc)],
                                           allow_ports=True)
            assert info["shared"] == ["libc.so.6"], info
            assert "abs" in info["imports"] and "malloc" in info["imports"], info
            exe = tdp / "release_libc.elf"
            exe.write_bytes(blob)
            r = subprocess.run([*P8._WSLCMD, "bash", "-lc",
                                f"chmod +x {P8._wsl_path(exe)} && {P8._wsl_path(exe)}"],
                               capture_output=True, text=True, timeout=120, shell=False)
            assert r.returncode == 7, (
                f"发行版产物跑出 {r.returncode}（期望 7）：{r.stderr[-300:]!r}")
            # ---- 仍然关着的那条：谁都没有的名字 ⇒ 链接期点名拒
            bad = probe.with_name("release_bad.lomt")
            bad_src = PROBE_RELEASE_LIBC.replace(
                "extern fn abs(v: i32) -> i32;",
                "extern fn abs(v: i32) -> i32;\n"
                "extern fn loment_nobody_provides(v: i32) -> i32;", 1).replace(
                "let v: i32 = abs(0 as i32 - 7);",
                "let v: i32 = abs(0 as i32 - 7) + loment_nobody_provides(0 as i32);", 1)
            bad.write_text(bad_src, encoding="utf-8", newline="\n")
            mod2 = lomentc.load(bad)
            deps2 = lomentc.resolve_deps(mod2, ROOT, tdp, entry=bad)
            ir2 = lomentc.emit_llvm(mod2, ROOT, deps2)
            try:
                lomelf.compile_ll(ir2, [lomelf.load_foreign(libc)], allow_ports=True)
                raise AssertionError("没人提供的名字居然链过去了 —— 开闸开过头了")
            except lomelf.Unsupported as e:
                assert "没有导出" in str(e), str(e)
        finally:
            loment_channel.RELEASE = old
    print("      发行版 + 真 libc.so.6：链成动态产物、跑出 7；没人提供的名字仍然点名拒")


def main() -> int:
    return loment_suite.run(TESTS, "loment_channel_test")


if __name__ == "__main__":
    sys.exit(main())
