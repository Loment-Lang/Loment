# 212 · 许可：Loment-Open（已废）→ MIT OR Apache-2.0

> 状态: **已被取代**（2026-10-19，用户裁）· 落点: 根 `LICENSE` / `LICENSE-APACHE` 与七个登记处
>
> 一句话: 2026-10-09 用过一版自造的 `Loment-Open`（MIT 加署名与名称两条）；
> **2026-10-19 起改成 `MIT OR Apache-2.0` 双许可**，两条附加条件一并取消。
> 旧文本只在 git 历史里；这一页保留它为什么被换掉的记录。

## 0. 现在的许可（2026-10-19 起）

Loment 以 **MIT OR Apache-2.0** 双许可发布，使用者二选一：

* 根 `LICENSE` —— **MIT 全文**（`Copyright (c) 2026 FujoOS Project`）；
* 根 `LICENSE-APACHE` —— Apache License 2.0 标准全文；
* `editors/vscode/LICENSE` —— 与根 `LICENSE` **逐字节相同**（`vsce` 只找扩展目录那一份，
  判据 `vscode_ext_test::test_vscode_publish_ready` 钉住）。

SPDX 标识就是 `MIT OR Apache-2.0`。两个都是 OSI 认证许可，npm / vsce / Cargo 都认得。

**为什么根 `LICENSE` 里没有那句"二选一"**：GitHub 的许可检测只认**纯正文**。正文前面
加一段自定义声明，那份文件会被判成 `Other`，侧栏就既不显示 MIT 也不显示 Apache-2.0
（实测于 2026-10-19）。所以双许可的"二选一"写在 `README`、`CONTRIBUTING.md` 与
`editors/vscode/package.json` 的 SPDX 字段里；`LICENSE` 与 `LICENSE-APACHE` 各自是
**可被检测器认出的纯文本**。

## 1. 为什么换掉 Loment-Open

2026-10-09 那一版是 MIT 原文加两条附加：派生源码必须署名 Loment（§2），名称保留（§3）。
当时就写清了代价（旧 §2）：**它不是 MIT、不是 OSI 开源许可、没有 SPDX 标识**，
对外说"Loment 是开源项目"要留神措辞。那一版是一次有意的实验，不是疏忽。

换掉的理由是那三条代价在一年里全都兑现了：

1. **没有 SPDX 标识** —— `"license": "Loment-Open"` 在 npm / vsce 眼里是未知值，
   打包告警；下游的许可扫描器只能把它标成 `NOASSERTION`。
2. **不是 OSI 开源许可** —— 对外说"开源"时始终要加括号解释，这与"把话说清楚"的方向相反。
3. **署名与名称两条没有带来可观测的收益** —— 派生作品本来就会在 git 历史与 README 里
   留痕；名称保护真正该去的地方是商标，不是版权许可。

**用户 2026-10-19 裁：改用 `MIT OR Apache-2.0`，不再用自己的许可。**
这也是 Rust 生态的惯例（`MIT OR Apache-2.0` 双许可，使用者按需选）。

## 2. 改了什么（登记处）

| # | 位置 | 改动 |
|---|---|---|
| 1 | 根 `LICENSE` | Loment-Open 全文 → **纯 MIT 全文** |
| 2 | 根 `LICENSE-APACHE` | **新增**，Apache-2.0 标准全文 |
| 3 | `README.md` / `README.zh-CN.md` | 徽章与 `## License` / `## 许可` 段 |
| 4 | `CONTRIBUTING.md` §Licence | 双许可；贡献按同一双许可收 |
| 5 | `tools/loment_publish.py` 的 README 模板 | 公开仓 README 是直出的，模板同步 |
| 6 | `editors/vscode/LICENSE` | 与根 `LICENSE` 逐字节相同 |
| 7 | `editors/vscode/package.json` / `package-lock.json` | `"license": "MIT OR Apache-2.0"` |
| 8 | `docs/171` / `docs/235` | 提到旧许可名的地方改成新事实 |

`loment_publish.py` 的发布包清单里 `LICENSE` 那一项不变；`LICENSE-APACHE` 随根目录一起进包
（发布口的文件清单按仓根展开）。

## 3. 边界（诚实清单）

1. **不主张这有法律意见。** 这一页是工程记录：记的是改了哪几个字节、为什么、代价在哪。
2. **换出口许可是版权人的处分权。** 旧 `CONTRIBUTING.md` 是按 Loment-Open 收的贡献；
   本仓今天的版权人只有 FujoOS Project，所以改得动。若将来有第三方贡献者按旧文本贡献过，
   这一条的边界要重新看（git 历史里有每一笔的作者）。
3. **名称保护不再写在许可里。** `Loment` 这个名字的使用约束不再由版权许可承载；
   要不要主张商标是另一件事，与本次改动无关。
4. **`lompi` 仓仍然没有 `LICENSE`。** `tools/loment_publish.py` 的 `lompi` 发布 spec 只切
   `lompi/` 与 skill，不切 `LICENSE` —— 那是它原本就有的洞，与换许可无关；要补是另一笔。
5. **不改 `lom/`、不动判据。** 这次只碰文本与登记处；`lom/*.lom` 是跨线契约（`CLAUDE.md`）。

## 4. 与其它文档的关系

* `CONTRIBUTING.md` §Licence —— 贡献者入口；这一页是它背后"为什么"的那份。
* `docs/201-doors-and-registries.md` —— "一个事实有几个登记处"的同一类问题。
* `docs/171-loment-publish-outlets.md` —— 发布口，说明了公开仓 README 为什么是直出的。
* 根 `LICENSE` / `LICENSE-APACHE` —— 正本；这一页只是说明，**改了文本这一页要跟着改**。
