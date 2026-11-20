# Potato v20：`systems` —— 六个系统的一处索引

> 2026-11-04。设计背景与验收见 `#1022`。

## 1. 为什么

v19 之前，"这份产物有哪些系统、各自是什么状态、花了多少"分散在七个并列字段里：
`gc` + `gc_ladder`、`runtime`、`port`、`vm`、`lms` + `lms_levels`、`lspe`。
字段之间有交叉检查，但它们散在不同套件里，而且**没有一处**说"这两个数描述的是同一件事"。

v20 加一个**索引**，让读者从一处开始读：

```json
"systems": {
  "gc":      {"state": "gc_auto",  "detail": "gc_ladder",  "cost": {"sites": 12}},
  "runtime": {"state": "runtime",  "detail": null,         "cost": null},
  "port":    {"state": "sealed",   "detail": null,         "cost": null},
  "vm":      {"state": "no_vm",    "detail": null,         "cost": null},
  "lms":     {"state": "lms_full", "detail": "lms_levels", "cost": {"levels": 7}},
  "lspe":    {"state": "lspe_open_admin", "detail": "lspe", "cost": {"sites": 4}}
}
```

`detail` 是**指针**，不是副本：它只写**已有字段的名字**，数字仍住在那一个字段里。
两份实现发出来的对象仍逐字节相同。

## 2. 三条不变量（校验器不读源码就判得了）

| 不变量 | 说明 |
|---|---|
| `state` | 标量系统等于顶层字段；`lspe` 等于台账自己的 `lspe.state` |
| `detail` | 有明细的系统必须是 `gc_ladder` / `lms_levels` / `lspe`；没有的必须是 `null` |
| `cost` | 有明细的系统是单键对象，值等于明细的总数；没有的必须是 `null` |

`cost` 的口径：

* `gc` → `gc_ladder.total_sites`（`{"sites": N}`）；
* `lms` → `lms_levels` 里为 `true` 的级数（`{"levels": N}`）；
* `lspe` → `lspe.sites`（`{"sites": N}`）。

"每个系统出现且只出现一次"：v20 的六个系统都是必填字段，所以索引**恒有六个键**；
未知系统名、缺一个系统都是错误。`boundary` / `surfaces` **不是系统** —— 它们是跨系统的
计数面，不在这张索引里。

## 3. 版本

`v20 = v19 + systems`。v19 及更早的对象**没有**这一格，仍然合法，也不许带它
（台阶只往上加，不回溯改写，`docs/147` §5）。

## 4. 判据

| 判据 | 钉什么 |
|---|---|
| `potato_test::test_v20_systems_index_cross_checks_every_link` | 正例合法；错 state / 错 cost / 缺系统 / 指错 detail / 多余键等 15 条反例；v19 对象仍合法且不许带 `systems` |
| `potato_test::test_lompotato_twin_matches_python` | 两份校验器对 v20 的判决与**错误条数**逐条一致 |
| `loment_potato_emit_test::test_potato_emit_matches_reference` | 两份发射器发出来的 v20 对象逐字节相同 |

校验器本体：`tools/potato.py`（`SYSTEM_KEYS` / `SYSTEM_DETAIL` / `SYSTEM_COST_KEY`、`VERSIONS` 里的 `v20`）；
孪生校验器：`loment/tools/lompotato.lomt` 的 `chk_systems`；
两份发射器：`tools/lomentc.py::emit_potato` 与 `loment/selfhost/potato.lomt::pt_systems`。
