#!/usr/bin/env python3
"""loment_potato_emit_test.py — 自举侧的 **Potato v5 发射** 逐字节判据（`docs/189` S1）。

对照面是 `tools/lomentc.py::emit_potato`：同一份源，自举驱动发的形式对象与参考实现发的
必须**逐字节相同**。形式对象不是"差不多就行"的那种产物 —— 它是**跨线契约的那一侧**
（`docs/147` §5 承诺过旧版可回放），差一个字节就是两个对象。

    stage1 <unit.lomt> --emit-potato     ←→     lomentc.emit_potato(...)

## 三条判据，各管一件事

1. `test_potato_emit_matches_reference` —— **语料上逐字节**。语料是**枚举的**
   （`COVERED`），而且 `test_every_example_is_decided` 钉住"`loment/examples` 下每一份
   要么在 `COVERED`、要么在 `REFUSED`、要么在 `SKIPPED` 里"。新加一份示例而没做决定会
   红在那一条 —— 这一格最怕的不是测错，是**新语料悄悄不测**。
2. `test_out_of_subset_is_refused_by_name` —— 子集外的每一条轴**点名拒绝**（退出码 1，
   话里出现那个轴的名字），而不是发一个空壳或者错壳出去。这一条是这一格的**下限**：
   绿在"覆盖的那片对"上不算数，还得保证"没覆盖的那片不会被静默糊过去"。
3. `test_seed_is_not_stale` —— 判据跑的是**种子链成的 stage1**。种子与树不同步时这一条
   先红，免得"绿"是在测上一版二进制（`loment_seed_test` 也管这件事，这里重说一遍是因为
   **这一格的绿完全依赖它**）。

用法: python tools/loment_potato_emit_test.py     （缺 clang 时 SKIP，退出码 0）
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import lomentc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "loment" / "examples"

#: 这一格**覆盖**的语料: 单文件（不带 `use`）、非泛型、无 trait/impl 的那一片。
#: 逐份与参考实现比字节 —— 不挑"有代表性"的几份。
COVERED = (
    "bootprobe.lomt",
    "bytes.lomt",
    # ---- 命令单元（`docs/223`）：两种写法各一份，形式对象与参考实现逐字节比
    "cmd_hello.lomt",           # 形态 B：`pub fn loment_command()` + `command_main`
    "register_hello.lomt",      # 形态 A：`register <名字> { … }`
    "mathutil.lomt",
    "native.lomt",
    "native_agg.lomt",
    "native_bits.lomt",
    "native_brk.lomt",
    "native_cap.lomt",          # capability + guard(2) + revocable
    "native_concat.lomt",
    "native_entry.lomt",
    "native_match_full.lomt",
    "native_mem.lomt",
    "native_mut.lomt",          # `mut [u32]` / `[u32]` 形参 —— 类型渲染的边界
    "native_slice.lomt",
    "native_str.lomt",
    "native_trait.lomt",        # trait + 两个 impl —— `traits`/`impls` 的原始视图 +
                                # impl 方法的 `self`（改名 `__self` + 填接受者类型）
    # ---- 泛型**函数/类型**的单态化（`instances` 那一格 + 函数表被替换）
    "all_loment.lomt",          # `max_of_u32` —— 实例名 = 基名 + 实参类型
    "native_res.lomt",          # M7: 预置 `Result<T, E>` 的实例化（`Result_u32_u32`）——
                                # 枚举实例 + 签名里的改名 + `generics` 那三组
    "native_gen_sig.lomt",      # M7: 泛型 struct 的实例化（`Box_i32` / `Box_u32`）+ 字段替换,
                                # **两份实例**用来钉住"按串排序"（不排就会分叉）
    "native_gen.lomt",          # **字段访问当泛型实参**（`max(p.a, p.b)`）—— 参考实现按
                                # **字段**类型推（`p.a` → `u32`，于是实例名 `max_u32`），
                                # 这一格 2026-09-23 起也这么推（`pt_field_arg_ty`）。
                                # 泛型 struct 的字段类型**就是那个类型形参**时要换成**声明处
                                # 写的实参** —— 那一段 token 本来就在单元里，指回去即可。
    "demo.lomt",                # M-L0: `use "lom/fujr.lom"` —— `layouts` 那一格要**读那个 L0 文件**
                                # 并解析它的 `record`（Header/Section；字段按偏移排）
    "tour.lomt",                # 同上；同时带着 `Entry`/`Kind` 两张大表
    "selfcheck.lomt",
    "switch.lomt",              # 开关取值要进对象: `switches` 那一格
    "toolchain.lomt",
    "user_hello.lomt",
    # ---- 带 `use` 的（`imports` 那一格 + **根/依赖的边界**）
    "ahci.lomt",                # 一个依赖
    "allocator.lomt",           # 一个依赖 + const
    "native_chain.lomt",        # **链式泛型**（泛型函数体里再调泛型函数）—— 2026-09-23 起
                                # 不拒了：`outer<T>` 的实例 `outer_u32` 的体里那句
                                # `pick(a, b)` 推得出 `pick_u32`（M6 改成跑到**不动点**，
                                # 与参考实现那 8 轮同构）。实例的**创建顺序**也要对上。
    "fuc_node.lomt",            # 一个依赖
    "lumtui_demo.lomt",         # 五个 `use` —— 依赖的**传递闭包**共 6 份（`lumtui_font`
                                # 是被 `lumtui_layout` 拉进来的），顺序也要对上
)

#: **点名拒绝**的那些（文件 -> 拒绝话里必须出现的那个轴的名字）。
#: 每一条都对应 `loment/selfhost/potato.lomt` 头上写的那几条边界。
#:
#: **2026-09-23 起这里是空的** —— 原来唯一那条是 `native_chain.lomt`（链式泛型）。
#: 它现在**不拒了**：自举侧的 M6 改成了**跑到不动点**（走单元里的非泛型声明，再逐轮走
#: 新建**实例的体**，形参类型按实参换掉），于是 `outer_u32` 的体里那句 `pick(a, b)`
#: 推得出 `pick_u32` —— 与参考实现那 8 轮同构。它进了 `COVERED`，逐字节相同。
REFUSED: dict[str, str] = {}

#: 连**检查**都还没过的（与这一格无关，但必须有一格，否则"没做决定"那条判据会把它当成漏网）。
SKIPPED = {
    "native_raii.lomt": "自举检查器还不认 `Drop`（trait/impl 那一格之前的事）",
}

#: `loment/examples` 之外再点几份 —— 它们各自钉住一处在示例里**覆盖不到**的边界。
EXTRA_COVERED = (
    # 常量值的量级。`num.lomt` 的 `4294967296` 与 `hash.lomt` 的 `14695981039346656037`
    # 都**装不进 u32** —— 这一格第一版按 u32 累加，就在这里发出了回绕过的数（`value: 0`）。
    # 这三份是那次实测的**回归钉子**，不是随手挑的语料。
    "lompi/store/std/0.1.0/num.lomt",
    "lompi/store/std/0.1.0/hash.lomt",
    "lompi/store/std/0.1.0/f64bits.lomt",
    # 工具链核心库（`use <名字>` 两边的名字冲突规则就是它们定的）
    "loment/lib/num.lomt",
    "loment/lib/mem.lomt",
    # 自举侧自己的 IR 单元：`[u8; N]` / 切片 / 大段 const 都有
    "loment/selfhost/ir_mem.lomt",
    "loment/selfhost/ir_for.lomt",
    "loment/tools/lomsyscalls.lomt",
    # ---- 带依赖的库与工具（`imports` 那一格在真实规模上的样子）
    "lompi/store/host/0.1.0/fs.lomt",       # 一个依赖
    "lompi/store/std/0.1.0/text.lomt",      # 两个依赖 —— `imports` 的顺序要按依赖序
    "loment/tools/lomcli.lomt",             # 三个依赖、31 KB 的对象（这一格最大的语料）
    "loment/lib/lumtui.lomt",               # 11 KB、一大批 const 与 struct
    # `addin` 拉进来的开关设定：这一份是**仓库级 sweep 抓出来的**——
    # 参考实现走 `load_unit`（带开关预扫）时 `switches` 里有 addin 那条定义，
    # 而直接 `load()` 只有根自己那份。它钉住"这一格是**整个程序**的表"。
    "loment/examples/addin/main.lomt",
    # **嵌套泛型实参**（`Outer<Inner<u32>>`）—— 2026-09-23 起**支持**（`docs/147` §实例命名
    # 定义了嵌套实例名：`Outer_Inner__u32`，里层用两个下划线）。这一份是那次改动的钉子：
    # 它同时钉住**名字的规则**（`test_nested_instance_name_follows_the_rule`）与
    # **逐字节**（这一份进 `COVERED` 就自动比）。
    "loment/examples/nested_gen/main.lomt",
    # **四层梯各来一站**（`gc_ladder`, `docs/210` §2/§3）—— 这一份是 v10 的钉子：它是**唯一**
    # 一份 `choose gc_auto_alpha` 的语料，于是 `gc_ladder` 的 `l0`/`l1`/`l2` 都非零，
    # 两个实现数出来的**组成**才真的被比过。别的语料全是 `gc_manual`，那里三个数恒为 0 ——
    # 只比那些，"自举侧重推四层"这件事从没被验过。
    "loment/examples/gc_ladder/main.lomt",
    # **联网的四档宽度各来一站**（`net_ladder`, `docs/237`）—— 与上一份同一个道理：树里
    # 别处**一处联网站点都没有**（`docs/218` §1④ 实测 0 处），只有这一份能让四个数都非零，
    # 于是"两张号表（面与宽度档）在两个实现里数得一样"才真的被比过。
    "loment/examples/net_ladder/main.lomt",
)

#: 语料之外单独点的拒绝轴：`comefor` 与外部代码块都由**驱动器**拒（那不是这一格的判断，
#: 但必须是"点名拒绝"而不是"发个壳出去"）。
EXTRA_REFUSED = {
    "loment/comefor/def_dialect.lomt": "comefor",
    "loment/extblock/evil.lomt": "external code blocks",
    # `choose write grammar python` 那种源：自举侧的前门本来就收不了（docs/188 §7.1），
    # 与这一格无关，但"收不了"也要看得见。
    "loment/lib/lumtui_math.lomt": "grammar",
    # **数组/切片当泛型实参**（`Box<[u32]>`）—— 与"两边都拒"那一类同形。2026-09-23 查它的顺序
    # 值得记：**先问这东西能不能存在，再吵它叫什么** —— 它**没有名字规则**（`Box_[u32]` 不是
    # 标识符），但更前面的是**后端 M23**（struct 字段暂只支持标量）挡着，`Box<ptr>` 就证明了
    # 这条：名字没问题，IR 照样拒。两个实现现在都**点名拒**。
    "loment/examples/arg_shape/main.lomt": "this shape of generic argument",
}

TESTS: list = []


def test(fn):
    TESTS.append(fn)
    return fn


def _stage1() -> Path | None:
    """种子 -> stage1（走发行包构建的同一条路）。seed 不新鲜时由 `_assert_seed_fresh` 报。"""
    try:
        import loment_dist  # noqa: PLC0415
        return loment_dist.build_stage1()
    except Exception as e:  # noqa: BLE001
        print(f"      SKIP: 拿不到 stage1 ({type(e).__name__}: {e})")
        return None


def _run(stage1: Path, rel: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(stage1), rel, "--emit-potato"], cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", shell=False, timeout=300)


def _want(rel: str) -> str:
    """参考实现给这份源发的对象 —— **走 CLI 那一个入口**（`lomentc.load_unit`）。

    两处不这么走就会**算的是另一件事**：

    * `load_unit` 里带**开关预扫**（`prescan_switches`）。直接 `load()` 的话
      `mod.switches` 只有**根自己那份**，而 `addin` 拉进来的开关设定不在里面 ——
      `loment/ examples/addin/main.lomt` 就是这么被扫出来的（参考 `[]`、
      自举侧有那条定义，两边"都算出了对象"却不是一个）。
    * `lom_root` 是 `--lom-root` 的默认值（**仓根**，不是 `loment/`）：`use "lom/fujr.lom"`
      那种 L0 布局是相对仓根找的，传错了会让 `layouts` 那一格静静地变成空表。
    """
    mod, deps = lomentc.load_unit(ROOT / rel, ROOT)
    return lomentc.emit_potato(mod, ROOT, deps)


@test
def test_seed_is_not_stale():
    """判据跑的是种子链成的 stage1 —— 种子过期就**先红这里**，别让绿落在旧二进制上。"""
    from loment_seed import reference_ir  # noqa: PLC0415
    seed = (ROOT / "loment" / "build" / "selfhost_driver.ll").read_text(encoding="utf-8")
    want = reference_ir()
    assert seed == want, (
        f"种子过期：盘上 {len(seed)}B vs 参考 {len(want)}B —— 跑 "
        f"`python tools/loment_seed.py --emit`（改了 driver 链就必须跟上）"
    )
    print(f"      种子与参考逐字符一致（{len(want)}B）")


@test
def test_potato_emit_matches_reference():
    """语料上逐字节相同 —— 这一格就是它。"""
    stage1 = _stage1()
    if stage1 is None:
        return
    ok = 0
    for name in COVERED:
        rel = f"loment/examples/{name}"
        want = _want(rel)
        r = _run(stage1, rel)
        assert r.returncode == 0, f"{name}: stage1 退出码 {r.returncode}，stderr={r.stderr[-300:]}"
        got = r.stdout
        if got != want:
            g, w = got.splitlines(), want.splitlines()
            i = next((k for k in range(min(len(g), len(w))) if g[k] != w[k]),
                     min(len(g), len(w)))
            raise AssertionError(
                f"{name}: 第 {i + 1} 行不同\n  盘: {g[i] if i < len(g) else '<eof>'!r}\n"
                f"  参: {w[i] if i < len(w) else '<eof>'!r}")
        ok += 1
    for rel in EXTRA_COVERED:
        want = _want(rel)
        r = _run(stage1, rel)
        assert r.returncode == 0, f"{rel}: stage1 退出码 {r.returncode}，stderr={r.stderr[-300:]}"
        got = r.stdout
        if got != want:
            g, w = got.splitlines(), want.splitlines()
            i = next((k for k in range(min(len(g), len(w))) if g[k] != w[k]),
                     min(len(g), len(w)))
            raise AssertionError(
                f"{rel}: 第 {i + 1} 行不同\n  盘: {g[i] if i < len(g) else '<eof>'!r}\n"
                f"  参: {w[i] if i < len(w) else '<eof>'!r}")
        ok += 1
    print(f"      {ok} 份单元的形式对象与参考实现逐字节相同")


@test
def test_out_of_subset_is_refused_by_name():
    """子集外必须**点名拒绝**：退出码 1 + 话里出现那个轴的名字。"""
    stage1 = _stage1()
    if stage1 is None:
        return
    n = 0
    for name, axis in REFUSED.items():
        rel = f"loment/examples/{name}"
        ref = None
        try:
            ref = _want(rel)
        except Exception:  # noqa: BLE001
            pass
        if ref is None:
            # 参考实现自己都发不出来（那这一份就不该挂在"拒绝"名下）—— 挪去 SKIPPED。
            raise AssertionError(f"{name}: 参考实现发不出对象，它不属于 `REFUSED`（挪到 `SKIPPED`）")
        r = _run(stage1, rel)
        assert r.returncode != 0, f"{name}: 在子集外却发了对象（静默的那种错）"
        assert axis in r.stderr, f"{name}: 拒绝话里没有轴 {axis!r}：{r.stderr[-200:]!r}"
        n += 1
    for rel, axis in EXTRA_REFUSED.items():
        r = _run(stage1, rel)
        assert r.returncode != 0, f"{rel}: 在子集外却发了对象"
        assert axis in r.stderr, f"{rel}: 拒绝话里没有轴 {axis!r}：{r.stderr[-200:]!r}"
        n += 1
    print(f"      {n} 份子集外的单元各自点名拒绝（没有一个静默发出去）")


@test
def test_nested_instance_name_follows_the_rule():
    """**嵌套泛型实参**（`Outer<Inner<u32>>`）：实例名按 `docs/147` §「实例命名」——
    **分隔的长度 = 嵌套深度**（一层 `_`、两层 `__`）。

    这条判据 2026-09-23 之前叫 `test_nested_generic_is_refused_by_both`，钉的是"**两边都
    发不出来**"：参考实现按旧规则把名字拼成 `Outer_Inner<u32>`（`<` `>` 还在里面，**不是
    标识符**），被它**自己的**形式对象自检拒掉。`docs/147` 定了命名规则之后两边都能发了，
    判据随之从"两边都拒"改成"**名字按规则**"。

    ⚠ 钉在**名字**上、不是"能跑就行"：`Outer_Inner__u32`（一个嵌套实参）与
    `Outer<Inner, u32>` → `Outer_Inner_u32`（两个实参）**不能撞名**，这条就是那道线 ——
    只写"发得出来"的话，撞名了也看不出来。
    """
    d = json.loads(_want("loment/examples/nested_gen/main.lomt"))
    got = {i["name"]: i["args"] for i in d["instances"] if i["kind"] == "type"}
    want = {"Inner_u32": ["u32"], "Outer_Inner__u32": ["Inner_u32"]}
    assert got == want, f"嵌套实例名/实参不符规则：{got}（应为 {want}）"
    print("      嵌套实例名：Outer<Inner<u32>> -> Outer_Inner__u32（与两实参的 …_u32 不撞）")


@test
def test_non_scalar_generic_arg_refused_by_name():
    """**数组/切片当泛型实参**（`Box<[u32]>`）：两个实现都**点名拒**，且参考侧**不再是内部错误**。

    这条钉两件事：

    1. **参考侧给的是有名字的拒绝**，不是"形式对象自检失败"那种崩（2026-09-23 之前它是后者：
    名字拼成 `Box_[u32]`、被它自己的校验器拒掉）。**内部错误是工具在说自己坏了** —— 修法
    是让它把话说清，不是让人去读自检输出。
    2. **理由里带着真正的卡点**：后端 `native M23`（struct 字段暂只支持标量）。顺序很重要 ——
    **先问这东西能不能存在，再争它叫什么**：`Box<ptr>` 是证据（名字完全没问题，IR 照样拒）。
    """
    rel = "loment/examples/arg_shape/main.lomt"
    p = ROOT / rel
    mod = lomentc.load(p)
    deps = lomentc.resolve_deps(mod, ROOT, p.parent, entry=p)
    assert not lomentc.check(mod, deps=deps), "夹具本身要能过检查 —— 出界的是形式对象那一层"
    got = None
    try:
        lomentc.emit_potato(mod, ROOT, deps)
    except Exception as e:  # noqa: BLE001
        got = str(e)
    assert got is not None, "参考实现居然发出来了？那这一格要重判"
    assert "形状" in got, f"参考侧的拒绝话里没有那个轴：{got[:200]}"
    assert "自检失败" not in got, f"参考侧又退回**内部错误**了（该是点名拒）：{got[:200]}"
    assert "M23" in got, f"拒绝话里该带上真正的卡点（后端 M23）：{got[:200]}"
    print("      Box<[u32]>：参考侧与自举侧都**点名拒**（真正卡点是后端 M23，不是命名）")


@test
def test_every_example_is_decided():
    """`loment/examples` 下**每一份**都得有一个说法：覆盖 / 拒绝 / 跳过。

    没有这一条，这一格的"绿"会被**新增一份没测到的示例**悄悄稀释 —— 而这一格最怕的
    正是"少测了却不知道"。新加一份示例时必须在这里显式表态。
    """
    decided = set(COVERED) | set(REFUSED) | set(SKIPPED)
    have = {p.name for p in EX.glob("*.lomt")}
    undecided = sorted(have - decided)
    assert not undecided, f"这些示例还没被这一格表态（加进 COVERED/REFUSED/SKIPPED）: {undecided}"
    stale = sorted(decided - have)
    assert not stale, f"这些名字在列表里但文件没了: {stale}"
    missing = sorted(m for m in SKIPPED if m not in have)
    assert not missing, f"SKIPPED 里有不存在的文件: {missing}"
    print(f"      {len(have)} 份示例全部有说法（{len(COVERED)} 覆盖 / "
          f"{len(REFUSED)} 拒绝 / {len(SKIPPED)} 跳过）")


def main() -> int:
    return loment_suite.run(TESTS, "loment_potato_emit_test")


if __name__ == "__main__":
    sys.exit(main())
