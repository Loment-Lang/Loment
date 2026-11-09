#!/usr/bin/env python3
"""域几何：从代码里读出**域树**与**域图**（`docs/315` §4–§5）。

这份工具不改变语言，也不进产物 —— 它把已经写在代码里的边读出来，给判据与后续的
Potato 字段一个确定的形状：

* **竖向树**：`domain` 块的嵌套链（父 → 子），以及链上每一层的空间 / 形状 / 时律；
* **横向树**：同一父下的兄弟域、`if`/`match` 的分支、`domain:for` 的位置轴（含 `step`）；
* **平面图**：顶点 = 域实例，边 = `contains`（竖向包含）/ `sibling`（同层并列）/
  `flow`（同一层里按声明顺序的先后）/ `guard`（一条守卫用了哪个域的权威）；
* **立体图**：在平面图之上加第三轴（`quality`：`adaptive` 域的质量）与跨层边
  （父域直接连到孙域）。

输出的 JSON 是**确定性**的：节点按 (函数, 审计槽号) 排，边按 (from, to, kind) 排，
不依赖 dict 迭代顺序。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import lomentc


@dataclass
class Node:
    id: str
    fn: str
    name: str
    space: str
    lo: int
    hi: int
    kind: int
    param: int
    depth: int
    parent: str = ""
    adaptive: bool = False     # 立体图的第三轴：这条域的质量可调


@dataclass
class Edge:
    src: str
    dst: str
    kind: str                  # contains | sibling | flow | guard | cross
    line: int = 0


@dataclass
class Loop:
    fn: str
    domain: str
    var: str
    step: int
    positions: int
    line: int


@dataclass
class SolidBody:
    """立体图的**体**（`docs/315` §5.2）：`solid 名 : A, B;` 的成员与根。"""
    name: str
    root: str
    members: list


@dataclass
class Geometry:
    nodes: list = field(default_factory=list)
    edges: list = field(default_factory=list)
    loops: list = field(default_factory=list)
    branches: list = field(default_factory=list)
    solids: list = field(default_factory=list)

    def to_json(self) -> str:
        nodes = sorted((n.__dict__ for n in self.nodes), key=lambda d: d["id"])
        edges = sorted((e.__dict__ for e in self.edges),
                       key=lambda d: (d["src"], d["dst"], d["kind"]))
        loops = sorted((l.__dict__ for l in self.loops),
                       key=lambda d: (d["fn"], d["line"], d["var"]))
        branches = sorted(self.branches)
        solids = sorted((x.__dict__ for x in self.solids), key=lambda d: d["name"])
        return json.dumps({"nodes": nodes, "edges": edges,
                           "loops": loops, "branches": branches, "solids": solids},
                          ensure_ascii=False, indent=1, sort_keys=True)


def _step_of(while_node) -> int:
    """`While.step` 里的更新步：`VAR = VAR + N` → N（缺省 1）。"""
    for st in getattr(while_node, "step", []):
        if isinstance(st, lomentc.Assign) and isinstance(st.expr, lomentc.Bin) \
                and st.expr.op == "+" and isinstance(st.expr.right, lomentc.IntLit):
            return st.expr.right.value
    return 1


def geometry(mod: lomentc.Module) -> Geometry:
    """把一份已解析的单元读成几何。审计槽号来自 `_resolve_domains`（要先跑 check/emit）。"""
    # 审计槽号 / 链 / 联动组都由这一趟填；它是幂等的，重复调用只会重算同一组号。
    lomentc._resolve_domains(mod)
    g = Geometry()
    # 顶层 capability 也是图的顶点（联动 / 互斥主要就长在它们之间）。
    cap_node = {}
    for i, c in enumerate(mod.caps):
        n = Node(id=f"cap:{i}", fn="", name=c.name, space=c.space, lo=c.lo, hi=c.hi,
                 kind=lomentc.CAP_KIND_ID[c.kind], param=c.param, depth=-1,
                 adaptive=c.kind == "adaptive")
        g.nodes.append(n)
        cap_node[c.name] = n
    for fn in mod.funcs:
        stack: list[Node] = []
        seq: dict[int, list[str]] = {}     # 父层 -> 这一层的节点（按声明顺序）

        def node_of(blk, depth: int, parent: str) -> Node:
            return Node(id=f"{fn.name}#{blk.audit_id}", fn=fn.name, name=blk.name or "?",
                        space=blk.space, lo=blk.lo, hi=blk.hi,
                        kind=lomentc.CAP_KIND_ID[blk.kind], param=blk.param,
                        depth=depth, parent=parent,
                        adaptive=blk.kind == "adaptive")

        def walk(stmts, floor: int, scope: list) -> None:
            prev: str = ""
            for s in stmts:
                if isinstance(s, lomentc.DomainBlock):
                    parent = stack[-1].id if stack else ""
                    n = node_of(s, len(stack), parent)
                    g.nodes.append(n)
                    if parent:
                        g.edges.append(Edge(parent, n.id,
                                            "child_of" if getattr(s, "child_of", "")
                                            else "contains", s.line))
                    if scope:
                        g.edges.append(Edge(scope[-1], n.id, "sibling", s.line))
                    scope.append(n.id)
                    if prev:
                        g.edges.append(Edge(prev, n.id, "flow", s.line))
                    prev = n.id
                    stack.append(n)
                    walk(s.body, floor + 1, [])
                    stack.pop()
                elif isinstance(s, lomentc.If):
                    g.branches.append(f"{fn.name}:{s.line}")
                    walk(s.then, floor + 1, [])
                    walk(s.otherwise, floor + 1, [])
                elif isinstance(s, lomentc.Match):
                    for _pat, arm in s.arms:
                        walk(arm, floor + 1, [])
                elif isinstance(s, (lomentc.While, lomentc.For)):
                    # prov = ((变量, 空间, 域名), …)：并行的 `domain:for i, j in a, b`
                    # 一轮里每个域各算一条（`docs/315` §4.2）。
                    for prov in getattr(s, "proven", ()) or ():
                        target = next((n for n in reversed(stack)
                                       if n.name == prov[2] and n.space == prov[1]), None)
                        if target is None:
                            continue
                        step = abs(_step_of(s)) or 1
                        span = target.hi - target.lo + 1
                        g.loops.append(Loop(fn.name, target.id, prov[0], step,
                                            (span + step - 1) // step, s.line))
                    walk(s.body, floor + 1, [])
                elif isinstance(s, lomentc.Solid):
                    ids = []
                    for nm in s.names[1:]:
                        tgt = next((x for x in stack if x.name == nm), None)
                        if tgt is not None:
                            ids.append(tgt.id)
                        elif nm in cap_node:
                            ids.append(cap_node[nm].id)
                    for i in range(len(ids)):
                        for j in range(i + 1, len(ids)):
                            a_, b_ = sorted((ids[i], ids[j]))
                            g.edges.append(Edge(a_, b_, "couple", s.line))
                    if ids:
                        g.solids.append(SolidBody(s.names[0], ids[0], ids))
                elif isinstance(s, (lomentc.Couple, lomentc.Mutex)):
                    # 图的边（`docs/315` §5）：联动 `⇄` / 互斥 `⊥`。只记**块级**节点 ——
                    # 顶层 capability 不在几何的顶点集里。
                    ids = []
                    for nm in s.names:
                        tgt = next((x for x in stack if x.name == nm), None)
                        if tgt is not None:
                            ids.append(tgt.id)
                        elif nm in cap_node:
                            ids.append(cap_node[nm].id)
                    for i in range(len(ids)):
                        for j in range(i + 1, len(ids)):
                            a_, b_ = sorted((ids[i], ids[j]))
                            g.edges.append(Edge(
                                a_, b_, "couple" if isinstance(s, lomentc.Couple) else "mutex",
                                s.line))
                elif isinstance(s, lomentc.Guard) and s.is_block:
                    # 守卫用它所在的块（最内层）作为使用者
                    user = stack[-1].id if stack else ""
                    target = next((n for n in stack if n.name == s.cap), None)
                    if user and target is not None and user != target.id:
                        g.edges.append(Edge(target.id, user, "guard", s.line))
                elif isinstance(s, lomentc.Quality) and s.is_block:
                    if stack:
                        stack[-1].adaptive = True
                elif isinstance(s, lomentc.Discharge) and s.is_block:
                    user = stack[-1].id if stack else ""
                    target = next((n for n in stack if n.name == s.cap), None)
                    if user and target is not None:
                        g.edges.append(Edge(target.id, user, "flow", s.line))

        walk(fn.body, 0, [])

    # 立体图：跨层边（父 → 孙）。平面图只画同层，这条边只在立体图里存在。
    ids = {n.id: n for n in g.nodes}
    for n in g.nodes:
        p = ids.get(n.parent)
        if p is not None and p.parent in ids:
            g.edges.append(Edge(p.parent, n.id, "cross", 0))
    return g


def main(argv: list[str]) -> int:
    import sys
    from pathlib import Path
    if len(argv) < 2:
        print("用法: python tools/loment_geometry.py FILE.lomt")
        return 2
    p = Path(argv[1]).resolve()
    mod = lomentc.load(p)
    deps = lomentc.resolve_deps(mod, p.parent.parent, p.parent, entry=p)
    lomentc.check(mod, deps=deps)
    lomentc._resolve_domains(mod)
    print(geometry(mod).to_json())
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv))
