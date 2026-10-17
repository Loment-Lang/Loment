#!/usr/bin/env python3
# potato_test.py — Potato 独立校验器完备性自检 (M47, docs/147)
#
# 判据: 校验器不 import 编译器代码; 规范里的每条规则都有反例被拒。
# 运行: python tools/potato_test.py   (退出码 0 = 全绿)
#
# 本文件只 import potato —— 若它需要编译器才能判合法, 独立性就已失效。

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import potato  # noqa: E402

# `lomentc` / `lomelf` **只**用来把孪生编出来（S1 第十五格）—— **判合法与否一次都不经过
# 编译器**：上面那条 `test_validator_does_not_import_compiler` 扫的是 `potato.py` 自己，
# 那条纪律照旧。这里多出来的两个 import 只是为了把 `lompotato.lomt` 链成可执行文件。
import lomentc  # noqa: E402
import lomelf  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


def fixture() -> dict:
    """一份覆盖 v1 全字段的合法形式对象 (手写, 不经编译器)。"""
    return {
        "potato": "v1",
        "unit": "demo",
        "language": "loment",
        "imports": ["mathutil"],
        "capabilities": [
            {"name": "blk_write", "domain": {"space": "disk", "lo": 0, "hi": 4},
             "revocable": True},
        ],
        "functions": [
            {"name": "sum", "params": [{"name": "xs", "type": "[u32]"}], "ret": "u32"},
            {"name": "fill", "params": [{"name": "xs", "type": "mut [u32]"},
                                        {"name": "v", "type": "u32"}], "ret": "()"},
            {"name": "len_of", "params": [{"name": "s", "type": "str"}], "ret": "u32"},
            {"name": "first", "params": [{"name": "b", "type": "Blk"}], "ret": "u32"},
            {"name": "make", "params": [], "ret": "Pair_u32"},
        ],
        "layouts": [
            {"name": "Header", "size": 16, "endian": "little", "packed": True,
             "fields": [{"name": "magic", "type": "u32", "offset": 0},
                        {"name": "len", "type": "u32", "offset": 4}]},
        ],
        "consts": [{"name": "MAX", "type": "u32", "value": 8}],
        "enums": [
            {"name": "Color", "variants": ["Red", "Green"]},
            {"name": "Opt_u32", "variants": ["None", "Some"], "payloads": {"Some": "u32"}},
        ],
        "types": [
            {"name": "Blk", "fields": [{"name": "off", "type": "u32"}]},
            {"name": "Pair_u32", "fields": [{"name": "a", "type": "u32"},
                                            {"name": "b", "type": "u32"}]},
        ],
        "traits": [{"name": "Measurable", "methods": ["measure"]}],
        "impls": [
            {"trait": "Measurable", "for": "Blk", "methods": ["measure"]},
            {"trait": "Drop", "for": "Blk", "methods": ["drop"]},
        ],
        "generics": [
            {"kind": "fn", "name": "max", "params": ["T"]},
            {"kind": "type", "name": "Pair", "params": ["T"]},
        ],
        "instances": [
            {"kind": "fn", "name": "max_u32", "of": "max", "args": ["u32"]},
            {"kind": "type", "name": "Pair_u32", "of": "Pair", "args": ["u32"]},
        ],
        "guards": 2,
        "excluded": ["network: 本单元不申请任何 net 能力"],
    }


def rejected(doc: dict, needle: str) -> bool:
    errs = potato.validate(doc)
    return any(needle in e for e in errs), errs


# ---------------------------------------------------------------- 独立性 (M47 判据 1)

@test
def test_validator_does_not_import_compiler():
    src = (Path(potato.__file__)).read_text(encoding="utf-8")
    for m in re.finditer(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", src, re.M):
        assert m.group(1).split(".")[0] != "lomentc", m.group(0)
    assert "lomentc" not in src.replace("# M47: 本文件不得 import 编译器 (lomentc)", "")


@test
def test_fixture_is_valid():
    assert potato.validate(fixture()) == [], potato.validate(fixture())


# ---------------------------------------------------------------- 反例表 (M47 判据 2)
# 每条 = (名字, 变异函数, 期望错误片段); M53 跨实现一致性复用同一张表。

MUTATORS = [
    # 版本串必须是**认得的**那几版之一；`v99` 不认得，所以这条测的是"认版本"本身。
    # （2026-10-10 改：原先钉的是 `v11`，而 v11 从这次起是**合法**版本 —— 那条夹具
    #  于是不再报"版本"，改报缺字段，测的就不是这条规则了。同一天 `v12` 也合法了，
    #  所以这条夹具改钉**永远不认得**的 `v99`，免得每升一版都要来动它。）
    ("version", lambda d: d.__setitem__("potato", "v99"), "版本"),
    ("v0 禁扩展字段", lambda d: d.__setitem__("potato", "v0"), "未知顶层字段"),
    ("v1 必填 generics", lambda d: d.pop("generics"), "缺字段 generics"),
    ("函数返回类型", lambda d: d["functions"][0].__setitem__("ret", "u9"), "ret 非法类型"),
    ("切片元素类型", lambda d: d["functions"][0]["params"][0].__setitem__("type", "[u9]"),
     "参数类型非法"),
    ("可变切片元素类型", lambda d: d["functions"][1]["params"][0].__setitem__("type", "mut [Foo]"),
     "参数类型非法"),
    ("泛型参数空", lambda d: d["generics"][0].__setitem__("params", []),
     "params 必须是非空数组"),
    ("泛型参数重复", lambda d: d["generics"][0].__setitem__("params", ["T", "T"]), "重复"),
    ("泛型声明重复", lambda d: d["generics"].append(dict(d["generics"][0])), "泛型声明重复"),
    ("实例引用未知泛型", lambda d: d["instances"][0].__setitem__("of", "nope"),
     "不是已声明的泛型"),
    ("实例实参数量", lambda d: d["instances"][0].__setitem__("args", []), "不符"),
    ("实例实参类型", lambda d: d["instances"][0].__setitem__("args", ["u9"]), "非法类型"),
    ("实例名重复", lambda d: d["instances"].append(dict(d["instances"][0])), "重复"),
    ("impl 引用未知 trait", lambda d: d["impls"][0].__setitem__("trait", "Nope"),
     "不是已声明的 trait"),
    ("impl 方法未声明", lambda d: d["impls"][0]["methods"].append("ghost"), "未在 trait"),
    ("impl for 非法类型", lambda d: d["impls"][0].__setitem__("for", "Nope"), "for 非法类型"),
    ("trait 方法空", lambda d: d["traits"][0].__setitem__("methods", []),
     "methods 必须是非空数组"),
    ("枚举载荷未声明", lambda d: d["enums"][1]["payloads"].__setitem__("Some", "Nope"),
     "类型未声明"),
    ("载荷键非变体", lambda d: d["enums"][1]["payloads"].__setitem__("Nope", "u32"),
     "不是已声明变体"),
    ("常量类型", lambda d: d["consts"][0].__setitem__("type", "str"), "必须是整型"),
    ("能力域区间", lambda d: d["capabilities"][0]["domain"].__setitem__("lo", 9), "区间非法"),
    ("布局重叠", lambda d: d["layouts"][0]["fields"][1].__setitem__("offset", 0), "重叠"),
    ("布局越界", lambda d: d["layouts"][0]["fields"][1].__setitem__("offset", 15), "越界"),
    ("类型引用未声明", lambda d: d["types"][0]["fields"][0].__setitem__("type", "Nope"), "未声明"),
    ("函数重名", lambda d: d["functions"].append(dict(d["functions"][0])), "重复"),
    ("excluded 空串", lambda d: d.__setitem__("excluded", [""]), "非空字符串"),
    ("guards 负数", lambda d: d.__setitem__("guards", -1), "非负整数"),
    ("guards 类型", lambda d: d.__setitem__("guards", "2"), "非负整数"),
    # 跨表/跨列表探针 (M53: 两份独立实现最容易在这里分歧)
    ("类型与枚举同名", lambda d: d["types"][0].__setitem__("name", "Color"), "同时出现在"),
    ("能力名重复", lambda d: d["capabilities"].append(dict(d["capabilities"][0])), "重复"),
    ("枚举名重复", lambda d: d["enums"].append(dict(d["enums"][0])), "重复"),
    ("类型名重复", lambda d: d["types"].append(dict(d["types"][0])), "重复"),
    ("布局名重复", lambda d: d["layouts"].append(dict(d["layouts"][0])), "重复"),
    # v2 = v1 + 项目模式 (docs/143 §3.2)。**`mode` 必填** —— docs/175 §8 的判据就是
    # "从形式对象里删掉该字段, 独立校验器必须红", 而"必填"正是**不能往 v1 加字段**的
    # 原因: 那会让既有的 v1 对象 (含冻结样本 demo.v0.json 那一路) 全变非法, 而 v0/v1
    # 是承诺过能回放的 (docs/147 §5)。所以升版本。
    ("v2 缺 mode", lambda d: d.__setitem__("potato", "v2"), "mode 必须是"),
    ("v2 mode 拼错", lambda d: (d.__setitem__("potato", "v2"),
                            d.__setitem__("mode", "fast")), "mode 必须是"),
    ("v2 mode 非字符串", lambda d: (d.__setitem__("potato", "v2"),
                                d.__setitem__("mode", 1)), "mode 必须是"),
    # v3 = v2 + **开关取值** (docs/182 §1)。用户 2026-09-17: "开关的取值是要进 Potato 的"。
    # 同一条纪律: **必填, 可为空数组** —— 不存在"缺这项"的形态, 所以"这份单元是在什么
    # 开关状态下编的"是可回放的, 不是"看当时的源码猜"。
    ("v3 缺 switches", lambda d: (d.__setitem__("potato", "v3"),
                              d.pop("switches", None)), "switches 必须是数组"),
    ("v3 switches 不是数组", lambda d: (d.__setitem__("potato", "v3"),
                                 d.__setitem__("switches", {})), "switches 必须是数组"),
    ("v3 on 不是布尔", lambda d: (d.__setitem__("potato", "v3"),
                              d.__setitem__("switches", [{"name": "a", "on": 1}])),
     "必须是布尔"),
    ("v3 开关名非法", lambda d: (d.__setitem__("potato", "v3"),
                             d.__setitem__("switches", [{"name": "1x", "on": True}])),
     "name 非法"),
    ("v3 开关名重复", lambda d: (d.__setitem__("potato", "v3"),
                             d.__setitem__("switches", [{"name": "a", "on": True},
                                                        {"name": "a", "on": False}])),
     "name 重复"),
    # v4 = v3 + **方言**（`docs/184` §9 S4.3）：这份单元用了 `comefor` 定义的那些语法。
    # 同一条纪律：**必填、可为空数组**。`body` 是定义处那段程序的**源文本** ——
    # 只记名字的话，读产物的人知道"用了 `def`"却不知道 `def` 是什么（`docs/184` §7 ②）。
    # 这几条都先补上 `switches`（v4 继承 v3 的检查），把要测的那一条**孤立出来**。
    ("v4 缺 dialects", lambda d: (d.__setitem__("potato", "v4"),
                              d.__setitem__("switches", []), d.pop("dialects", None)),
     "dialects 必须是数组"),
    ("v4 dialects 不是数组", lambda d: (d.__setitem__("potato", "v4"),
                                 d.__setitem__("switches", []),
                                 d.__setitem__("dialects", {})), "dialects 必须是数组"),
    ("v4 方言名非法", lambda d: (d.__setitem__("potato", "v4"),
                             d.__setitem__("switches", []),
                             d.__setitem__("dialects", [{"name": "1x", "body": ""}])),
     "name 非法"),
    ("v4 方言名重复", lambda d: (d.__setitem__("potato", "v4"),
                             d.__setitem__("switches", []),
                             d.__setitem__("dialects", [{"name": "a", "body": ""},
                                                        {"name": "a", "body": ""}])),
     "name 重复"),
    ("v4 body 不是字符串", lambda d: (d.__setitem__("potato", "v4"),
                                 d.__setitem__("switches", []),
                                 d.__setitem__("dialects", [{"name": "a", "body": 1}])),
     "body 必须是字符串"),
    # v5 = v4 + **外部代码块**（`docs/185` §7 ①）：这份单元里嵌了别语言的正文。
    # 必填、可为空数组；**按源里的顺序**（序列，与 `dialects` 那个集合不同）。
    # 下面每一条都把前面几版的必填字段补齐，**只留要测的那一条**是坏的。
    ("v5 缺 bodies", lambda d: (d.__setitem__("potato", "v5"), _full(d),
                             d.pop("bodies", None)), "bodies 必须是数组"),
    ("v5 bodies 不是数组", lambda d: (d.__setitem__("potato", "v5"), _full(d),
                                 d.__setitem__("bodies", {})), "bodies 必须是数组"),
    ("v5 语言名非法", lambda d: (d.__setitem__("potato", "v5"), _full(d),
                             d.__setitem__("bodies", [{"lang": "1c", "body": ""}])),
     "lang 非法"),
    ("v5 body 不是字符串", lambda d: (d.__setitem__("potato", "v5"), _full(d),
                                 d.__setitem__("bodies", [{"lang": "c", "body": 1}])),
     "body 必须是字符串"),
]


def _full(d: dict) -> dict:
    """把**前几版的必填字段**补齐 —— 只留要测的那一条是坏的（否则报的是别的东西）。"""
    d.setdefault("mode", "std")
    d.setdefault("switches", [])
    d.setdefault("dialects", [])
    return d


def mutated_objects():
    """(名字, 非法对象) —— 供跨实现一致性对照 (M53)。"""
    for name, mut, _ in MUTATORS:
        d = fixture()
        mut(d)
        yield name, d


def fixture_v7() -> dict:
    """一份合法的 **v7** 形式对象: 上面的 v1 fixture + v6 的 `grammar` + v7 的 `boundary`。

    **为什么必须单独来一份**：`fixture()` 是 **v1**, 而 `boundary` 从 v7 起才合法 ——
    拿 v1 去试, 得到的只会是"未知顶层字段", 而校验器里那几条**规则**（分量必须是非负整数、
    总数要等于三项之和、不认识的键要报）**一条都碰不到**。判据扫过去一行不进分支,
    那种绿是空转。
    """
    d = fixture()
    d["potato"] = "v7"
    d["grammar"] = "loment"
    d["boundary"] = {"extern_declared": 1, "extern_calls": 2, "syscalls": 3,
                     "ptr_transforms": 4, "total_sites": 9}
    return d


def fixture_v8() -> dict:
    """一份合法的 **v8** 形式对象: v7 的再 + v8 的 `gc`（`docs/175` §3.4）。

    与 `fixture_v7` 同一个理由单独来一份：`gc` 从 v8 起才合法 —— 拿 v7 去试只会得到
    "未知顶层字段"，校验器里那几条规则（档名必须是那两个之一、"两档不能同时选"）
    **一条都碰不到**。
    """
    d = fixture_v7()
    d["potato"] = "v8"
    d["gc"] = "gc_manual"
    return d


def fixture_v9() -> dict:
    """一份合法的 **v9** 形式对象: v8 的再 + v9 的 `runtime`（`docs/175` §3.6）。

    与 `fixture_v7` / `fixture_v8` 同一个理由单独来一份：`runtime` 从 v9 起才合法 ——
    拿 v8 去试只会得到"未知顶层字段"，校验器里那几条规则（取值必须是那两个之一、
    `no_runtime` 不能与自动回收并存）**一条都碰不到**。
    """
    d = fixture_v8()
    d["potato"] = "v9"
    d["runtime"] = "no_runtime"
    return d


def fixture_v10() -> dict:
    """一份合法的 **v10** 形式对象: v9 的再 + v10 的 `gc_ladder`（`docs/210` §3/§5）。

    与 `fixture_v7` … `fixture_v9` 同一个理由单独来一份：`gc_ladder` 从 v10 起才合法 ——
    拿 v9 去试只会得到"未知顶层字段"，校验器里那几条规则（四个分量必须是非负整数、
    四数之和要等于 `total_sites`、不认识的键要报）**一条都碰不到**，那种绿是空转。
    """
    d = fixture_v9()
    d["potato"] = "v10"
    d["gc_ladder"] = {"l0": 1, "l1": 2, "l2": 3, "l3": 4, "total_sites": 10}
    return d


def fixture_v11() -> dict:
    """一份合法的 **v11** 形式对象: v10 的再 + v11 的 `port`（`docs/222` §4）。

    与 `fixture_v7` … `fixture_v10` 同一个理由单独来一份：`port` 从 v11 起才合法 ——
    拿 v10 去试只会得到"未知顶层字段"，校验器里那几条规则（两个取值、两条冲突）
    **一条都碰不到**，那种绿是空转。
    """
    d = fixture_v10()
    d["potato"] = "v11"
    d["port"] = "sealed"
    return d


def fixture_v12() -> dict:
    """一份合法的 **v12** 形式对象: v11 的再 + v12 的 `surfaces`（`docs/224`）。

    与 `fixture_v7` … `fixture_v11` 同一个理由单独来一份：`surfaces` 从 v12 起才合法 ——
    拿 v11 去试只会得到"未知顶层字段"，校验器里那几条规则（面名得是标识符、值得是非负
    整数、各面之和要等于 `total_sites`、以及 `total_sites` 要等于 `boundary.syscalls`）
    **一条都碰不到**，那种绿是空转。

    这里的数**刻意与 `fixture_v7` 的 `boundary.syscalls`（=3）对上** —— 交叉不变量是
    v12 独有的那一条，正例里就得先成立，否则测的就不是"自洽"而是"必然报错"。
    """
    d = fixture_v11()
    d["potato"] = "v12"
    d["surfaces"] = {"file": 1, "net": 2, "total_sites": 3}
    return d


#: v11 的 `port` 那几条规则的反例（`docs/222` §4）。作用在 **v11** 的对象上。
MUTATORS_V11 = [
    ("port 缺这一项", lambda d: d.pop("port"), "port 必须是"),
    ("port 取值拼错", lambda d: d.__setitem__("port", "hosted_nope"), "port 必须是"),
    ("port 不是字符串", lambda d: d.__setitem__("port", 1), "port 必须是"),
    # **两条冲突** —— 校验器独立判得了（两个取值都在对象里，不需要读源码）。
    ("hosted + no_std", lambda d: (d.__setitem__("port", "hosted"),
                                   d.__setitem__("mode", "no_std")), "不能同时选"),
    ("hosted + gc_auto_alpha", lambda d: (d.__setitem__("port", "hosted"),
                                          d.__setitem__("gc", "gc_auto_alpha")),
     "不能同时选"),
    # 反面：`hosted` **不**与 `gc_manual` / `gc_auto` 冲突 —— 这一条钉住"别把这一对也拒了"。
    # 它**不该**报错，所以当合法样本（见 `_cases()`），不在这张反例表里。
]


#: v12 的 `surfaces` 那几条规则的反例（`docs/224`）。作用在 **v12** 的对象上。
#:
#: 与 `MUTATORS_V10` 同形，但**多一条 `boundary` 那边没有的**：`surfaces.total_sites`
#: 必须等于 `boundary.syscalls`。那是 v12 唯一一条**跨字段**的自洽要求 ——
#: 每个机调用站点落在恰好一个面里，所以两处数的是同一批站点。
MUTATORS_V12 = [
    ("surfaces 整个缺掉", lambda d: d.pop("surfaces"), "surfaces 必须是对象"),
    ("surfaces 不是对象", lambda d: d.__setitem__("surfaces", []),
     "surfaces 必须是对象"),
    ("surfaces total_sites 缺掉", lambda d: d["surfaces"].pop("total_sites"),
     "surfaces.total_sites 必须是非负整数"),
    ("surfaces total_sites 不是整数", lambda d: d["surfaces"].__setitem__("total_sites", "x"),
     "surfaces.total_sites 必须是非负整数"),
    ("surfaces 面名不是标识符", lambda d: d["surfaces"].__setitem__("no such face", 0),
     "不是标识符"),
    ("surfaces 面是负数", lambda d: d["surfaces"].__setitem__("net", -1),
     "必须是非负整数"),
    ("surfaces 面不是整数", lambda d: d["surfaces"].__setitem__("net", "x"),
     "必须是非负整数"),
    # **自洽**：和数只在分量都合法时才去对。
    ("surfaces 各面之和对不上", lambda d: d["surfaces"].__setitem__("total_sites", 9),
     "各面之和"),
    # **交叉不变量**：和数与 `boundary.syscalls` 各说各话时，校验器读不到源码也判得出打架。
    # 这一条**故意让和数自洽**（`2+3=5`），这样报的只能是交叉那一条。
    ("surfaces 与 boundary.syscalls 打架",
     lambda d: d.__setitem__("surfaces", {"file": 2, "net": 3, "total_sites": 5}),
     "boundary.syscalls"),
]


def fixture_v13() -> dict:
    """一份合法的 **v13** 形式对象: v12 的再 + v13 的 `vm`（`docs/231`）。

    与 `fixture_v7` … `fixture_v12` 同一个理由单独来一份：`vm` 从 v13 起才合法 ——
    拿 v12 去试只会得到"未知顶层字段"。

    **这里没有冲突可判**，这是 v13 与前面几维不一样的地方：`vm` 与 `runtime` 今天确实
    合不来（VM 要用运行期），但那条要**读源码**（源码里用没用到运行期构造）—— 对象里
    判不了。所以它的反例只有"缺 / 拼错 / 不是字符串"三条，**没有**"不能同时选"那一条。
    """
    d = fixture_v12()
    d["potato"] = "v13"
    d["vm"] = "no_vm"
    return d


#: v13 的 `vm` 那几条规则的反例（`docs/231`）。作用在 **v13** 的对象上。
MUTATORS_V13 = [
    ("vm 缺这一项", lambda d: d.pop("vm"), "vm 必须是"),
    ("vm 取值拼错", lambda d: d.__setitem__("vm", "vm_t0"), "vm 必须是"),
    ("vm 不是字符串", lambda d: d.__setitem__("vm", 1), "vm 必须是"),
    # **反面**：`vm` = `"vm"` 是**合法**的（dev 通道）—— 它不该报错，所以当合法样本
    # （见 `_cases()`），不在这张反例表里。**发行版拒它是编译器的事，不是校验器的事**：
    # 对象里没有"渠道"这一栏，校验器也判不了它（`docs/231` §5 判据 D 落在打包侧）。
]


def fixture_v14() -> dict:
    """一份合法的 **v14** 形式对象: v13 的再 + v14 的 `net_ladder`（`docs/237`）。

    与 `fixture_v7` … `fixture_v13` 同一个理由单独来一份：`net_ladder` 从 v14 起才合法 ——
    拿 v13 去试只会得到"未知顶层字段"，校验器里那几条规则（四档得是非负整数、四档之和要
    等于 `total_net`、`total_net` 要等于 `surfaces.net`、不认识的键要报）**一条都碰不到**。

    这里的数**刻意与 `fixture_v12` 的 `surfaces.net`（=2）对上** —— 交叉不变量是 v14
    独有的那一条，而且比 `surfaces` 那条更深一层（那条是"和 == 另一个字段"，这条是
    "**和 == 另一个字段的一个分量**"），正例里就得先成立，否则测的不是"自洽"而是"必然报错"。
    """
    d = fixture_v13()
    d["potato"] = "v14"
    d["net_ladder"] = {"w0": 0, "w1": 1, "w2": 1, "w3": 0, "total_net": 2}
    return d


#: v14 的 `net_ladder` 那几条规则的反例（`docs/237`）。作用在 **v14** 的对象上。
#:
#: 与 `MUTATORS_V10`（`gc_ladder`）同形，**多一条 `gc_ladder` 那边没有的跨字段要求**：
#: `net_ladder.total_net` 必须等于 `surfaces.net` —— 每个联网站点落在一档里，而它在
#: `surfaces` 里落在 `net` 面里，所以两处数的是**同一批站点**（少一档 = 有的联网站点没归类，
#: 多一档 = 给不联网的站点配了宽度）。
MUTATORS_V14 = [
    ("net_ladder 整个缺掉", lambda d: d.pop("net_ladder"), "net_ladder 必须是对象"),
    ("net_ladder 不是对象", lambda d: d.__setitem__("net_ladder", []),
     "net_ladder 必须是对象"),
    ("net_ladder 缺一档", lambda d: d["net_ladder"].pop("w2"),
     "net_ladder.w2 必须是非负整数"),
    ("net_ladder 一档不是整数", lambda d: d["net_ladder"].__setitem__("w1", "x"),
     "net_ladder.w1 必须是非负整数"),
    ("net_ladder 一档是负数", lambda d: d["net_ladder"].__setitem__("w3", -1),
     "net_ladder.w3 必须是非负整数"),
    ("net_ladder total_net 缺掉", lambda d: d["net_ladder"].pop("total_net"),
     "net_ladder.total_net 必须是非负整数"),
    # **自洽**：和数只在四档都合法时才去对。
    ("net_ladder 四档之和对不上", lambda d: d["net_ladder"].__setitem__("total_net", 9),
     "四档之和"),
    # **交叉不变量**：这一条**故意让和数自洽**（`1+2=3`），于是报的只能是"与 `surfaces.net`
    # 打架"那一条 —— 否则测的是两件事撞在一起。
    ("net_ladder 与 surfaces.net 打架",
     lambda d: d.__setitem__("net_ladder",
                             {"w0": 1, "w1": 2, "w2": 0, "w3": 0, "total_net": 3}),
     "surfaces.net"),
    ("net_ladder 多一个键", lambda d: d["net_ladder"].__setitem__("w4", 0),
     "不认识的键"),
]



#: v8 的 `gc` 那几条规则的反例（`docs/175` §3.4）。作用在 **v8** 的对象上。
MUTATORS_V8 = [
    ("gc 缺这一项", lambda d: d.pop("gc"), "gc 必须是"),
    ("gc 档名拼错", lambda d: d.__setitem__("gc", "gc_nope"), "gc 必须是"),
    ("gc 不是字符串", lambda d: d.__setitem__("gc", 3), "gc 必须是"),
    # **两档不能同时选** —— 这一条是校验器**独立判得了**的（两个取值都在对象里）。
    ("no_std + gc_auto", lambda d: (d.__setitem__("mode", "no_std"),
                                    d.__setitem__("gc", "gc_auto")), "不能同时选"),
]


#: v9 的 `runtime` 那几条规则的反例（`docs/175` §3.6）。作用在 **v9** 的对象上。
MUTATORS_V9 = [
    ("runtime 缺这一项", lambda d: d.pop("runtime"), "runtime 必须是"),
    ("runtime 取值拼错", lambda d: d.__setitem__("runtime", "runtime_nope"),
     "runtime 必须是"),
    ("runtime 不是字符串", lambda d: d.__setitem__("runtime", 1), "runtime 必须是"),
    # **定义上矛盾** —— 校验器独立判得了（两个取值都在对象里）。
    ("no_runtime + gc_auto", lambda d: (d.__setitem__("runtime", "no_runtime"),
                                        d.__setitem__("gc", "gc_auto")), "不能同时选"),
    # 反面：`gc_manual` **不**与 `no_runtime` 冲突 —— 这一条钉住"别把这一对也拒了"。
    # 它**不该**报错，所以放进 `_cases()` 当合法样本（见那里），不在这张反例表里。
]


#: v10 的 `gc_ladder` 那几条规则的反例（`docs/210` §3/§5）。作用在 **v10** 的对象上。
#: 与 `MUTATORS_V7` 的 `boundary` 一一对应 —— 两者是**同一形状**（四个分量 + 总数自洽）。
MUTATORS_V10 = [
    ("gc_ladder 缺一项", lambda d: d["gc_ladder"].pop("l2"), "gc_ladder.l2"),
    ("gc_ladder 分量是负数", lambda d: d["gc_ladder"].__setitem__("l3", -1),
     "gc_ladder.l3"),
    ("gc_ladder 分量不是整数", lambda d: d["gc_ladder"].__setitem__("l0", "x"),
     "gc_ladder.l0"),
    # **自洽**（`docs/210` §5 第一行）—— 四数之和必须等于 `total_sites`。校验器读不到源码，
    # 判不了"数得对不对"，但判得了"它们自相矛盾"。
    ("gc_ladder 总数对不上", lambda d: d["gc_ladder"].__setitem__("total_sites", 99),
     "total_sites"),
    ("gc_ladder 多一个键", lambda d: d["gc_ladder"].__setitem__("extra", 0), "不认识的键"),
    ("gc_ladder 不是对象", lambda d: d.__setitem__("gc_ladder", []),
     "gc_ladder 必须是对象"),
    ("gc_ladder 整个缺掉", lambda d: d.pop("gc_ladder"), "gc_ladder 必须是对象"),
]




#: v7 的 `boundary` 那几条规则的反例（`docs/205` R5）。**单独一张表**：
#: 它们要作用在 **v7** 的对象上 —— 作用在 v1 上只会得到"未知顶层字段", 那测的是别的规则。
MUTATORS_V7 = [
    ("boundary 缺一项", lambda d: d["boundary"].pop("syscalls"), "boundary.syscalls"),
    ("boundary 分量是负数", lambda d: d["boundary"].__setitem__("ptr_transforms", -1),
     "boundary.ptr_transforms"),
    ("boundary 总数对不上", lambda d: d["boundary"].__setitem__("total_sites", 99),
     "total_sites"),
    ("boundary 多一个键", lambda d: d["boundary"].__setitem__("extra", 0), "不认识的键"),
    ("boundary 不是对象", lambda d: d.__setitem__("boundary", []), "boundary 必须是对象"),
    ("boundary 整个缺掉", lambda d: d.pop("boundary"), "boundary 必须是对象"),
]


@test
def test_every_spec_rule_has_a_rejection_case():
    assert len(MUTATORS) >= 24, len(MUTATORS)
    for name, mut, needle in MUTATORS:
        d = fixture()
        mut(d)
        errs = potato.validate(d)
        assert any(needle in e for e in errs), (name, errs)
    # v7 那一组作用在 **v7** 的对象上（理由见 `fixture_v7`）。
    for name, mut, needle in MUTATORS_V7:
        d = fixture_v7()
        mut(d)
        errs = potato.validate(d)
        assert any(needle in e for e in errs), (name, errs)
    # v8 那一组同理（`gc`）。
    for name, mut, needle in MUTATORS_V8:
        d = fixture_v8()
        mut(d)
        errs = potato.validate(d)
        assert any(needle in e for e in errs), (name, errs)
    # v9 那一组同理（`runtime`）。
    for name, mut, needle in MUTATORS_V9:
        d = fixture_v9()
        mut(d)
        errs = potato.validate(d)
        assert any(needle in e for e in errs), (name, errs)
    # v10 那一组同理（`gc_ladder`, docs/210 §3/§5）。
    for name, mut, needle in MUTATORS_V10:
        d = fixture_v10()
        mut(d)
        errs = potato.validate(d)
        assert any(needle in e for e in errs), (name, errs)
    # v11 那一组同理（`port`, docs/222 §4）。
    for name, mut, needle in MUTATORS_V11:
        d = fixture_v11()
        mut(d)
        errs = potato.validate(d)
        assert any(needle in e for e in errs), (name, errs)
    # v12 那一组同理（`surfaces`, docs/224）。
    for name, mut, needle in MUTATORS_V12:
        d = fixture_v12()
        mut(d)
        errs = potato.validate(d)
        assert any(needle in e for e in errs), (name, errs)
    # v13 那一组同理（`vm`, docs/231）。
    for name, mut, needle in MUTATORS_V13:
        d = fixture_v13()
        mut(d)
        errs = potato.validate(d)
        assert any(needle in e for e in errs), (name, errs)


# ---------------------------------------------------------------- 版本回放 (M51 前置)

@test
def test_v0_object_still_validates():
    """旧版本 (v0) 对象必须仍能回放校验 —— 只是不允许 v1 新字段。"""
    d = fixture()
    for k in ("traits", "impls", "generics", "instances", "guards"):
        d.pop(k)
    d["potato"] = "v0"
    d["functions"] = d["functions"][:1]
    assert potato.validate(d) == [], potato.validate(d)


@test
def test_v2_object_validates_with_mode():
    """v2 = v1 + `mode` (docs/143 §3.2)。**当前版本**, 所以它必须是能过的正例;
    反例 (缺 mode / 拼错 / 非字符串) 在 `MUTATORS` 里 —— 一正一反才说明"必填"是真的。"""
    d = fixture()
    d["potato"] = "v2"
    for m in potato.MODES:
        d["mode"] = m
        assert potato.validate(d) == [], (m, potato.validate(d))


@test
def test_committed_objects_validate():
    """仓库里每份已提交的形式对象都必须合法 (M45/M46 产物面)。"""
    objs = sorted((ROOT / "loment" / "build").glob("*.potato.json"))
    assert len(objs) >= 15, [p.name for p in objs]
    for p in objs:
        doc = json.loads(p.read_text(encoding="utf-8"))
        assert potato.validate(doc) == [], (p.name, potato.validate(doc))


@test
def test_m51_legacy_v0_replays():
    """M51: 旧版本 (v0) 对象按自带版本回放校验, 新版本不受影响。"""
    legacy = ROOT / "loment" / "build" / "legacy" / "demo.v0.json"
    doc = json.loads(legacy.read_text(encoding="utf-8"))
    assert doc["potato"] == "v0"
    assert potato.validate(doc) == [], potato.validate(doc)
    assert potato.main(["replay", str(legacy)]) == 0
    assert potato.main(["replay", "--expect-version", "v1", str(legacy)]) == 1


@test
def test_m48_llm_arm_scorer_offline():
    """M48: 宿主 LLM 臂的**建载荷 + 计分**离线可验 (不联网、不需要模型)。

    用罐装回复钉住计分语义 —— 三种典型失败模式各一条: 完整对象 / 原样抄模板 /
    实体畸形。真跑模型是外部一步 (docs/147 §8 的协议要求), 不进门禁。
    """
    import tempfile
    import potato_llm_arm as arm

    good = ('{"potato":"v1","unit":"m","language":"python","imports":[],"capabilities":[],'
            '"layouts":[],"functions":[{"name":"add","params":[{"name":"a","type":"u32"}],'
            '"ret":"u32"}],"consts":[],"enums":[],"types":[],"traits":[],"impls":[],'
            '"generics":[],"instances":[],"guards":0,"excluded":[]}')
    # 抄模板 (llama3.2:3b 的真实失败模式): 结构合法但没有本单元的任何实体
    echo = ('{"potato":"v1","unit":"<标识符>","language":"python","imports":[],'
            '"capabilities":[],"layouts":[],"functions":[],"consts":[],"enums":[],"types":[],'
            '"traits":[],"impls":[],"generics":[],"instances":[],"guards":0,"excluded":[]}')
    # 畸形实体 (qwen3:4b 的真实失败模式之一): 函数缺 ret
    # (连前面的逗号一起去掉, 否则剩下 `],}` 是非法 JSON —— 那测的就成了"抠不出 JSON")
    bad_entity = good.replace(',"ret":"u32"', '')

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        entries = {e["path"]: e for e in arm.corpus_entries()}
        path = "tools/potato.py"
        assert path in entries, sorted(entries)[:3]
        e = entries[path]
        meta = {"protocol": "test", "arm": "host-llm", "models": ["fake:1"],
                "files": [{"path": e["path"], "lang": e["lang"], "stem": "x"}]}
        (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        sub = d / arm.slug("fake:1")
        sub.mkdir(parents=True)
        (sub / "x.meta.json").write_text(json.dumps(
            {"path": e["path"], "lang": e["lang"],
             "chars_total": 10, "chars_sent": 10}), encoding="utf-8")
        for tag, text in (("good", good), ("echo", echo), ("bad", bad_entity)):
            (sub / f"x.rep.json").write_text(json.dumps({"message": {"content": text}}),
                                             encoding="utf-8")
            out = d / f"{tag}.json"
            assert arm.score_dir(d, out) == 0
            row = json.loads(out.read_text(encoding="utf-8"))["rows"][0]
            if tag == "good":
                assert row["object_valid"], row["validate_errors"]
                assert row["entities_ok"] == 1 and row["entities_malformed"] == 0, row
            elif tag == "echo":
                # 原样抄模板: 结构字段齐全, 但 unit 是占位符 `<标识符>` -> 校验器判非法,
                # 且里面没有任何本单元实体 (这一格正是"抄模板"与"真转写"的分界)
                assert not row["object_valid"], "占位符 unit 必须被判非法"
                assert row["entities_seen"] == 0, "模板里没有本单元实体"
            else:
                assert row["entities_ok"] == 0 and row["entities_malformed"] == 1, row

    # 载荷必须确定性 (同温度 0 同一 seed), 且语料路径不能越出仓库
    with tempfile.TemporaryDirectory() as td:
        assert arm.build(Path(td), ["fake:1"], 200, 1) == 0
        req = json.loads(next(Path(td).glob("fake_1/*.req.json")).read_text(encoding="utf-8"))
        assert req["options"] == {"temperature": 0, "seed": arm.SEED, "num_ctx": arm.NUM_CTX}
        assert "```" in req["messages"][0]["content"], "载荷里要有源码围栏"
    try:
        bad = json.loads(json.dumps([{"path": "../../etc/passwd", "lang": "python"}]))
        with tempfile.TemporaryDirectory() as td:
            corpus = Path(td) / "corpus.json"
            corpus.write_text(json.dumps(bad), encoding="utf-8")
            old, arm.CORPUS = arm.CORPUS, corpus
            try:
                arm.corpus_entries()
                raise AssertionError("越界路径没被拦住")
            except ValueError:
                pass
            finally:
                arm.CORPUS = old
    except AssertionError:
        raise


@test
def test_m50_assert_table_matches_objects():
    """M50: A1–A4 断言表由形式对象驱动, 且与已提交产物一致。"""
    import potato_assert
    objs = potato_assert.load_objects()
    rs = potato_assert.rows(objs)
    assert rs, "断言表为空"
    for r in rs:
        assert r["a1"] and r["a2"] and r["a3"] and r["a4"], r
    dest = ROOT / "loment" / "build" / "cap_asserts.rs"
    # S3 之后产物**不在索引里**（`docs/189` §3.0）：不在盘上是**默认状态**，不是缺陷 ——
    # 要钉的是"盘上那份（如果有）与生成结果一致"，也就是**漂移检得出来**。
    if dest.exists():
        assert dest.read_text(encoding="utf-8") == potato_assert.emit_rust(objs)


# ---------------------------------------------------------------- Loment 版（S1 第十五格）

TWIN = ROOT / "loment" / "tools" / "lompotato.lomt"


def _twin_exe(td: Path) -> Path:
    """把 `lompotato.lomt` 链成可执行文件（走仓库自己的原生后端，不经 clang）。

    它只吃 JSON、只用 open/read/write/brk/exit 五个系统调用，所以能在本机**原生**跑 ——
    不必进 WSL（与 `loment_lib_test` 的孪生同一条路）。
    """
    mod = lomentc.load(TWIN)
    deps = lomentc.resolve_deps(mod, ROOT, TWIN.parent, entry=TWIN)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"lompotato.lomt 自己检查不过: {errs[:2]}"
    ir = lomentc.emit_llvm(mod, ROOT, deps)
    exe = td / ("lompotato.exe" if sys.platform == "win32" else "lompotato")
    exe.write_bytes((lomelf.compile_pe(ir) if sys.platform == "win32"
                     else lomelf.compile_ll(ir))[0])
    exe.chmod(0o755)
    return exe


def _cases() -> list[tuple[str, dict]]:
    """判据这一侧的那批对象 —— **两条判据共用一份**（各写一遍必然漂）。

    * 合法: `fixture` / v0 / v2（两个 mode）/ 仓库里**已提交**的形式对象 / 冻结样本；
    * 非法: `MUTATORS` 那 51 条（每条钉一条规则）。
    """
    out: list[tuple[str, dict]] = [("fixture", fixture()), ("v7", fixture_v7()),
                                   ("v8", fixture_v8()), ("v9", fixture_v9()),
                                   ("v10", fixture_v10()), ("v11", fixture_v11()),
                                   ("v12", fixture_v12())]
    # **`gc_manual` + `no_runtime` 是合法档**（"要运行期、但内存我自己管"的反面：
    # 不要运行期、手动回收）—— 它必须**不报错**。上面那张反例表只会钉"该拒的要拒",
    # 钉不住"不该拒的别拒"，所以这一条以**合法样本**的身份过孪生那一关。
    _ok = fixture_v9()
    _ok["runtime"] = "runtime"
    out.append(("v9-runtime-on", _ok))
    for name, mut, _ in MUTATORS:
        d = fixture()
        mut(d)
        out.append((name, d))
    # v7 那一组（`boundary`）也要过孪生那一关 —— 否则两份校验器在 v7 上从没被比过。
    for name, mut, _ in MUTATORS_V7:
        d = fixture_v7()
        mut(d)
        out.append((f"v7-{name}", d))
    # v8 那一组（`gc`）同理。
    for name, mut, _ in MUTATORS_V8:
        d = fixture_v8()
        mut(d)
        out.append((f"v8-{name}", d))
    # v9 那一组（`runtime`）同理。
    for name, mut, _ in MUTATORS_V9:
        d = fixture_v9()
        mut(d)
        out.append((f"v9-{name}", d))
    # v10 那一组（`gc_ladder`）同理。
    for name, mut, _ in MUTATORS_V10:
        d = fixture_v10()
        mut(d)
        out.append((f"v10-{name}", d))
    # v11 那一组（`port`）同理。
    for name, mut, _ in MUTATORS_V11:
        d = fixture_v11()
        mut(d)
        out.append((f"v11-{name}", d))
    # **`hosted` + `gc_manual` 是合法档**（"要对外、但内存我自己管"）—— 必须**不报错**。
    # 反例表只钉"该拒的要拒"，钉不住"不该拒的别拒"。
    _okp = fixture_v11()
    _okp["port"] = "hosted"
    out.append(("v11-hosted-gc_manual", _okp))
    # v12 那一组（`surfaces`）同理。
    for name, mut, _ in MUTATORS_V12:
        d = fixture_v12()
        mut(d)
        out.append((f"v12-{name}", d))
    # v13 那一组（`vm`, `docs/231`）同理。
    for name, mut, _ in MUTATORS_V13:
        d = fixture_v13()
        mut(d)
        out.append((f"v13-{name}", d))
    # **`vm` = `"vm"` 是合法档**（dev 通道："这份产物要由 VM 执行"）—— 必须**不报错**。
    # 反例表只钉"该拒的要拒"，钉不住"不该拒的别拒"。
    # 发行版拒它是**编译器**的事：对象里没有"渠道"这一栏，校验器判不了（`docs/231` §5 判据 D）。
    _okv = fixture_v13()
    _okv["vm"] = "vm"
    out.append(("v13-vm", _okv))
    # v14 那一组（`net_ladder`, `docs/237`）同理。
    for name, mut, _ in MUTATORS_V14:
        d = fixture_v14()
        mut(d)
        out.append((f"v14-{name}", d))
    d0 = fixture()
    for k in ("traits", "impls", "generics", "instances", "guards"):
        d0.pop(k)
    d0["potato"] = "v0"
    d0["functions"] = d0["functions"][:1]
    out.append(("v0", d0))
    for m in potato.MODES:
        dv = fixture()
        dv["potato"] = "v2"
        dv["mode"] = m
        out.append((f"v2-{m}", dv))
    for p in sorted((ROOT / "loment" / "build").glob("*.potato.json")):
        out.append((p.name, json.loads(p.read_text(encoding="utf-8"))))
    lg = ROOT / "loment" / "build" / "legacy" / "demo.v0.json"
    if lg.exists():
        out.append(("legacy/demo.v0.json", json.loads(lg.read_text(encoding="utf-8"))))
    return out


@test
def test_lompotato_twin_matches_python():
    """**Loment 版校验器与 Python 版判决逐条一致**（S1 第十五格，丙类最后一件）。

    同一份对象喂两边：`potato.validate` 给 (接受 / 拒绝 + 错误条数)，孪生给
    `ok` / `err N` —— **stdout 逐字节相同 + 退出码相同**。

    **比到这里就够了，不要比文案**：`potato.validate` 的每条错误里都插了 Python 值的
    `repr()`（`得到 {ver!r}` 这种），Loment 侧复现不了。文案那半由下面的 `MUTATORS` 表钉着
    （每条反例钉一个错误片段）—— 那一层是**规则**的判据，这一层是**两份实现一致**的判据。

    **两侧都不经过编译器**：`lompotato.lomt` 只吃 JSON，M47 的独立性照旧。
    """
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        exe = _twin_exe(td)
        cases = _cases()
        bad = []
        for i, (name, obj) in enumerate(cases):
            fp = td / f"c{i:03d}.json"
            fp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8",
                          newline="\n")
            errs = potato.validate(obj)
            want_rc = 0 if not errs else 1
            want_out = "ok\n" if not errs else f"err {len(errs)}\n"
            r = subprocess.run([str(exe), str(fp)], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", shell=False, timeout=120)
            if (r.returncode, r.stdout) != (want_rc, want_out):
                bad.append((name, want_rc, want_out.strip(), r.returncode,
                            r.stdout.strip(), r.stderr.strip()[:80], errs[:2]))
        assert not bad, (f"{len(bad)}/{len(cases)} 份判决不一致（前 3）:\n"
                         + "\n".join(f"  {n}: py=({a},{b!r}) 孪生=({c},{d!r}) {e} pyerrs={f}"
                                     for n, a, b, c, d, e, f in bad[:3]))
        n_ok = sum(1 for _, o in cases if not potato.validate(o))
        print(f"      {len(cases)} 份对象（{n_ok} 合法 / {len(cases) - n_ok} 非法）："
              f"判决与错误条数逐条一致")


@test
def test_lompotato_twin_selfhost_compiles():
    """`lompotato.lomt` 必须能走**种子自举链**编译，且产出的 IR 与参考实现**逐字节相同**。

    没有 clang 时跳过 —— stage1 要靠 clang 链一次。
    """
    clang = shutil.which("clang") or r"C:\Program Files\LLVM\bin\clang.exe"
    if not (clang and Path(clang).exists()):
        print("      SKIP: 无 clang")
        return
    import loment_dist  # noqa: E402
    stage1 = loment_dist.build_stage1()
    mod = lomentc.load(TWIN)
    deps = lomentc.resolve_deps(mod, ROOT, TWIN.parent, entry=TWIN)
    want = lomentc.emit_llvm(mod, ROOT, deps)
    r = subprocess.run([str(stage1), TWIN.relative_to(ROOT).as_posix()], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=False, timeout=600)
    assert r.returncode == 0, f"stage1 编译 lompotato.lomt 失败: {r.stderr[-400:]}"
    got = r.stdout.replace("\r\n", "\n")
    assert got == want, f"自举镜与参考的 IR 不一致 (want {len(want)}B got {len(got)}B)"
    print(f"      种子自举链编译 lompotato.lomt 成功，且 IR 与参考逐字节相同 ({len(want)}B)")


def main() -> int:
    return loment_suite.run(TESTS, "potato_test")


if __name__ == "__main__":
    sys.exit(main())
