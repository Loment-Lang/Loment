# `LomentItem` 标记：标准库不被社区包顶替，且 store 可以被整体绕开

> 2026-11-10。每个随包发的 `pkg.lomp` 里多一个 `origin()`；编译器拿它当闸；
> 同时给裸金属留一条**完全不看 store** 的路。

## 1 标记长什么样

每个 `lompi/store/<包>/<版本>/pkg.lomp` 末尾：

```rust
/// 标准库来源标记（机器读，用户不可见）：解析器只把带 `LomentItem` 的包当作
/// **内置标准库**；社区包里同名的模块不能顶替它。这不是密码学签名 ——
/// 改这份文件的人也能把它加回去；真正的完整性是内容哈希 + 发行签名。
pub fn origin() -> str {
    return "LomentItem";
}
```

"不可见"指它不出现在任何用户可见的面上：`docs/store-index.md` 只扫 `.lomt` 模块，
`loment store` 只列包名，公开 API 是模块里的 `pub fn` —— `pkg.lomp` 的这几个函数
从来不在其中。

## 2 它挡什么、不挡什么

挡的是**替换**与**同名顶替**：

1. **社区包顶替标准库**。名字解析里 `deps/<名字>` 原先排在内置 store 前面；保留名
   （`docs/980` 那五个）跳过这一层。现在保留名 = **写死的那几个 + store 里每一个包名**，
   所以 `deps/math`、`deps/dev` 都不再能盖住标准库。
2. **往 store 里塞一个同名包**。从自带 store 解析到的模块，所属包的清单必须带
   `LomentItem`；没有就报错，拒绝把它当标准库。

**不挡**的是"有人改了本地文件再补上标记"—— 那是本地信任边界之外的事。完整性仍然由
**内容哈希**（每个包的身份就是 Merkle 内容哈希）与**发行签名**（release 资产）负责。
这条标记是**声明 + 闸**，不是签名本身；把它说成签名会让真正的签名看起来可省。

## 3 裸金属：`LOMENT_NO_STORE=1`

Loment 必须能编裸金属，而 store 里的大多数包会拖运行期（`alloc` 之类）。所以解析器给了
一个总开关：

```
LOMENT_NO_STORE=1   # 或 on/yes/true
```

打开之后 `store_roots()` 返回空表：名字解析只剩**项目本地 `deps/`** 与**内置四根**。
`use std` 找不到就是找不到，不会"顺手"从 store 拉一份；标记闸自然也不会触发（没有
store 可查）。这条开关是**编译期**的，`lompi` 包管理器本身不受影响 —— 装库、看锁文件
照常，只是这次编译不往里看。

**为什么放在解析层而不是打包层**：裸金属构建的目标不是"少装几个包"，而是"这次编译
的可达集合必须能从源码完全说清楚"。只要解析器不看 store，构建就是封闭的。

## 4 判据

| 判据 | 钉住什么 |
| --- | --- |
| `loment_lompi_test::test_every_builtin_package_carries_the_lomentitem_mark` | 12 个 `pkg.lomp` 全部带标记（新包漏了这条不会静默） |
| `lomentc_test::test_store_package_without_the_lomentitem_mark_is_not_a_builtin` | 没标记的 store 包被拒，错误里点名 `LomentItem` |
| `lomentc_test::test_community_deps_cannot_take_a_builtin_package_name` | `deps/math` 盖不住 store 的 `math` |
| `lomentc_test::test_the_store_can_be_bypassed_entirely` | `LOMENT_NO_STORE=1` 下 store 根为空、`deps/` 照常、`use vec` 找不到 |

棘轮：包是"必须全部带标记"，所以新加的包（比如 `windows`）一旦漏了，第一条判据直接红。
