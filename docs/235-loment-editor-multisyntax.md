# 235 · 编辑器把七门表层语法认进来了（VS Code 扩展 0.2.0）

## 0. 起因

用户 2026-10-12：**「更新 Loment VS Code 插件」**，并点名四件事：把高亮补到当前语言面、
许可与 main 对齐、修扩展相关的那两条 issue（#166 / #28）、**完成多语法支持**。

先说清**更新前**的状态（免得把它当成"没装"）：本机 `~/.vscode/extensions/fujojtop.loment-0.1.0/`
与当时的树**逐字节相同**、`--doctor` 报 0 个问题 —— 也就是说"重装一遍"本身**不产生任何变化**。
真正过期的是**内容**：语法文件停在 2026-09-18，而语言面在那之后长出了
`choose write grammar` 声明（`docs/188`）、`set choose` / `addin`（`docs/182`）、
`command` / `foruse`（`docs/185`）、`register`（`docs/223`）、核心维 `choose gc_auto_alpha`
（`docs/175`）与内建 `syscall7`。

## 1. 落了什么

| 落点 | 改了什么 |
|---|---|
| `syntaxes/loment.tmLanguage.json` | 新增 `grammar-decl` / `switches` / `extlang` / `register` 四格；内建表与类型表**跟到当前语言面**（见 §3） |
| `syntaxes/loment-<规范名>.tmLanguage.json` | **新增七份**（c / cpp / java / csharp / go / python / natural） |
| `editors/vim/syntax/loment.vim` | 同一张词表跟着更新（`loment_editors_test` 交叉核对，漂了就红） |
| `src/grammar-detect.js` | **新增**：读文件头那行声明的纯模块（与参考实现逐条对拍） |
| `src/conf-ext.js` | **新增**：读 `loment.conf` 的 `source_ext`（同上） |
| `src/extension.js` | 开文档 / 文件头改动时按声明把文档切到 `loment-<规范名>`；自定义后缀认成 Loment |
| `package.json` | 七个语言 + 七份语法；`capabilities.untrustedWorkspaces`（#166）；`loment.extraExtensions`；命令 / 任务 / 断点的 `when` 与 `languages` 从 `== loment` 改成**家族判据** |
| `tools/loment_lsp.py` · `loment/tools/lsp.lomt` | **#28**：`completionProvider` 从 `{"triggerCharacters":[".",":"]}` 改成 `{}` |

版本 0.1.0 → **0.2.0**（多语法是新功能）。许可在 `origin/main` 上早已是 `MIT OR Apache-2.0`
（0.1.5 起；此前是 `Loment-Open`），本次作业从 `origin/main` 起，所以那一栏是**自动对齐**的
—— 本地的 0.1.0 只是落后 425 个提交。

## 2. 七门表层语法：为什么是"各门一个 `loment-<规范名>`"

**两种做法**：① 打开时把文档切成 VS Code **内建**语言（`python` / `c` / `go`…）；
② 各门立一个自己的语言 id，语法文件里借宿主的 scope。**选了 ②**，理由是**身份与分工**：

- 一份 `choose write grammar python` 的源**仍然是 Loment 程序** —— 状态栏该说 Loment，
  我们的编译 / 运行 / 调试命令与快捷键该照常对它生效。切到内建语言这三样**全部失效**
  （命令的 `when` 是按 `editorLangId` 过滤的），而那是用户会立刻撞上的回归。
- ② 的代价是每门一份语法文件 —— 但它们**只有两条规则**：认那行声明、`include` 宿主的
  `source.<lang>`。真正的着色全在宿主语法里（VS Code 的 TextMate 引擎**按 scope 名**
  解析 `include`，所以跨扩展也能拿到）。
- **宿主没装就优雅退化**：`source.python` 解析成空，那个文件只剩声明那一行着色。
  这是刻意的 —— 拿 Loment 的规则去染 Python 正文只会给出**看着像高亮**的错颜色。

`natural`（`docs/197`）那一门**没有宿主语法可借**（全世界没有哪份 TextMate 语法认识它），
所以它那份文件只有声明那一格。**不编一套自造的着色**是如实的做法。

### 2.1 声明检测：第二份实现，靠**对拍**钉住

`grammar-detect.js` 与 `tools/potato_from.py` 的 `find_decl` / `read_grammar_decl` 是
**同一套规矩的两份表达**（扩展不能起 Python）。判据
`vscode_ext_test::test_vscode_grammar_decl_matches_reference` 把**仓库里全部 769 份
`.lomt` / `.lom`**（枚举，不是手挑）加上一批边角同时喂给两边，逐条比
`(规范名, 声明成立吗)`；报错文案不比（那归编译器）。

**它当场抓到一个真分歧**：JS 那份的 `DECL` 正则少了 `m` 标志，于是 `^` 只认**整串**开头 ——
"声明不写在第 1 行"的源一律读不到。手挑的边角全在第 1 行，**全绿**；是
`loment/examples/cproject/bitkit.lomt`（前面一段块注释、第 12 行才是声明）把两边拉开的。
那条形状已经补进边角语料。**这条与 `docs/188` §1 那条教训是同一个**：对拍的价值全在"枚举"。

## 3. 高亮跟着语言面走：两处**摘掉**比"补上"更值得记

补上的是：`choose write grammar <别名>`（声明那一行）、`set choose <名>` / `choose <值>` /
`choose close <值>` / `addin <名>`、核心维取值（`std` / `no_std` / `gc_manual` / `gc_auto` /
`gc_auto_alpha` / `runtime` / `no_runtime`）、`command <语言>` / `foruse <语言>`、
`register <名>`、内建 `syscall7`。

**关键是"按形状认，不按词认"**：`set` / `choose` / `addin` **不是保留字**
（`tools/lomentc.py` 的 `_reject_nested_switch_decls` 把这条写在注释里：参考实现接受关键字
做标识符，`fn set() -> u32` 合法），所以把它们写成裸关键字会把 `let set: u32` 也染色。
TextMate 与 Vim 两份都按 `choose\s+\w+` 这样的**形状**匹配。

摘掉的两处是**高亮在说假话**：

| 摘掉 | 为什么 |
|---|---|
| `load16` / `load32` / `store16` / `store32` | 它们**不在** `tools/lomentc.py` 的 `BUILTINS` 里 —— 是 `loment/examples/bytes.lomt` 里的**普通函数**。留着会把用户函数染成内建色 |
| `usize` / `isize` | 它们**不是** L1 类型（`loment/tools/lomc.lomt` 里的 `usize` 全在字符串字面量里、是发出去的 Rust）。实测 `fn f() -> usize` 报"返回类型 usize 未声明" |

## 4. 自定义后缀（`source_ext`）也认

项目在 `loment.conf` 里把 L1 后缀改成 `.foo` 时（`tools/lomentc.py` 的 `source_ext_of`），
那些文件在 VS Code 里**原本一点高亮都没有**。现在：扩展从打开的文件逐级向上找
`loment.conf`、读 `source_ext`，把该后缀的文件切到 `loment`；另外给一个设置
`loment.extraExtensions`（给"没有 conf 也想认某个后缀"的场合）。

**激活靠 `workspaceContains:**/loment.conf`** —— 自定义后缀的项目必然有那份 conf，
所以它既是配置源、也是激活信号（`onLanguage` 事件按后缀触发，后缀不是 `.lomt` 就永远不会来）。

## 5. #166：未受信工作区

`package.json` 加：

```json
"capabilities": {
  "untrustedWorkspaces": {
    "supported": "limited",
    "restrictedConfigurations": ["loment.serverCommand", "loment.serverArgs",
      "loment.toolCommand", "loment.toolArgs", "loment.dapCommand", "loment.dapArgs",
      "loment.serverPath", "loment.dapScript"]
  },
  "virtualWorkspaces": { "supported": false }
}
```

前六个是 issue 点名的"命令 + 参数"；**后两个是同一类**（issue 没点，但症状一样）：
`serverPath` / `dapScript` 的取值最终会变成 `python <那个路径>`（或
`wsl -e python3 <那个路径>`）—— 工作区改它一个字段就等于换掉"要跑哪个程序"。

**落点两处**（issue 只要了判据那一条，这里多一处）：`vscode_ext_test::
test_vscode_hardens_untrusted_workspaces` 断言**内容**，`vscode_ext.check()` 里的
`MUST_RESTRICT` 断言**每次打包都会看**。选择 `restrictedConfigurations` 而不是 `"scope": "machine"`：
前者是 VS Code 为这件事定的机制（Restricted Mode 下忽略），受信工作区里**照旧可覆盖** ——
`scope: machine` 会把"每个项目自己的工具链路径"这种正当用法一起砍掉。

## 6. #28：把两个触发字符撤掉

`completionProvider` 从 `{"triggerCharacters": [".", ":"]}` 改成 `{}`。
**成员补全没实现之前，不广告触发字符才是诚实的那一半**（issue 自己指的路）：
补全不看位置，`p.` 后面回来的还是一屏关键字 —— 每敲一个点弹一屏没用的东西，读起来就是坏的。
手动 `Ctrl+Space` 照旧可用。两个实现（Python 与自举）**同一处改**。

顺带修了一条**判据的判法**：`loment_tools_test` 里原来是
`assert caps["definitionProvider"] and caps["completionProvider"]` ——
空 dict 在 Python 里是**假值**，而它表达的恰恰是"支持补全"。改成
`"completionProvider" in caps`：这里要问的是**在不在**，不是**真不真**。

## 7. 加一门表层语法要动的地方（扩展侧）

`docs/188` §7.1 那张登记处清单管的是**语言侧**；扩展这一侧另有四处，**都别漏**：

1. `potato_from.GRAMMAR_ALIASES`（语言侧，**唯一**的权威表）—— 扩展这份是它的第二份表达，
   判据对拍；
2. `package.json` 的 `languages` **与** `grammars`（各一条，一一对应 —— `vscode_ext.check()` 会抓）；
3. `syntaxes/loment-<规范名>.tmLanguage.json`（两行：认声明 + `include` 宿主 scope）；
4. 若那门的注释记号不是 `//`，还要一份 `language-configuration*.json`，**并且**
   `tools/vscode_ext.py` 的 `INCLUDE_FILES`、`tools/loment_release.py` 的 `GLOBS`、
   `loment/tools/lomrel.lomt` 的 `glob_text` **三处同插**（后两处**位置逐条对齐**）——
   漏了就是"装上去才发现"或"发布清单上隐形"。

`vscode_ext.check()` 现在会抓第 4 条的前半（语言引用的 `configuration` 必须存在**且在打包清单里**）。

## 8. 没做 / 开着

- **两个 LSP 都不读那行声明**（`tools/loment_lsp.py` / `loment/tools/lsp.lomt` 按原生 Loment
  解析）。所以外门那几种**不接语言服务**：接上去只会拿到一堆假诊断 ——
  比"没有补全"更坏。这**另记 issue**，带可复现证据。
- **宿主扩展没装时**，那份文件只有声明那一行着色（见 §2）。
- **人工点验还没做**：高亮换语言那一格由 `vscode_ext_test::test_vscode_surface_grammar_switches_language`
  真的开文档断言（不是纸面判据），但"在 VS Code 里肉眼看一眼配色"仍没人做过 ——
  与 `docs/154` 里 M56 记「部分」同一条理由。
- **`lomfmt` 对这类源是拒绝**（`refuse_foreign_grammar`），不是格式化 —— 这是**既定行为**，
  不是本次引入的。

## 9. 判据

```
python tools/vscode_ext_test.py        # 16/16（新增 7 条：对拍 ×2、换语言、多语法登记、#166、升级扫旧版）
python tools/loment_editors_test.py    # 3/3（vim 与 TextMate 词表交叉核对）
python tools/loment_lsp_test.py        # 8/8（含自举 LSP；#28 那处改动真的编进去跑了）
python tools/loment_tools_test.py      # 33/33
python tools/vscode_ext.py --check     # 0 个问题
python tools/vscode_ext.py --install   # 重打包 + 侧载到本机 VS Code
```
