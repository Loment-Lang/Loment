# Loment Studio

源码参数台：把一份 `.lomt` 里所有能调的东西摊成一张表，改一个值、看差异、再写回。
给 **Loment 自身的开发**用（编译器、自举源、标准库、新功能调参），不是应用构建器。

设计记录：[`docs/310`](../../docs/310-loment-studio.md)。

## 两个入口

### 命令行

```bash
cargo run -p loment-studio-cli -- list loment/selfhost/codegen.lomt
cargo run -p loment-studio-cli -- explain gc
cargo run -p loment-studio-cli -- set loment/selfhost/codegen.lomt const:CG_MAXFN 5200 --dry-run
cargo run -p loment-studio-cli -- matrix loment/selfhost/codegen.lomt
cargo run -p loment-studio-cli -- add  <file> const LIMIT u32 64 --help "上限"
cargo run -p loment-studio-cli -- check <file>
```

### 网页

```bash
cargo run -p loment-studio-server
# open http://127.0.0.1:8710
```

浏览器不解析 Loment：参数表、补丁预览、矩阵、校验全部由 Rust 后端回答。

## 能改什么

| 类 | 来源 |
|---|---|
| 语言环境 | `choose std/no_std`、`choose gc_*`、`choose runtime`、`choose write grammar X` |
| 自定义开关 | `set choose NAME { … }` + `choose NAME` / `choose close NAME` |
| 能力域 | `capability NAME : SPACE[LO..HI] revocable` |
| 常量 | 深度 0 的 `const NAME: TY = N;`（只收整数） |
| 项目配置 | `loment.conf` 的单行 `pub fn key() -> ty { return v; }` |

每一类都有说明：目录（`param-catalog.json`）→ 行尾注释 → 上方注释块 → “没有注释/引用次数”。
目录按名字或前缀（`CG_*`、`TB_*`）扩展，**新功能加参数先改目录，不用改代码**。

环境矩阵列出 `mode × gc × runtime` 的所有组合，并按编译器的规则标出冲突与理由。

## 安全边界

- 只做**外科式替换**：注释、空行、排版原样保留。
- 先预览再写盘；CLI 的 `--dry-run` 与网页的“应用”走同一个 `apply()`。
- 写回后 `lomentc --check` 校验。

## 目录

```
crates/params/   解析 / 拼接 / 新增 / 目录 / 矩阵（无 IO）
crates/cli/      loment-studio 命令
crates/server/   本地 HTTP：/api/{params,preview,apply,add,matrix,check,source}
web/             index.html · app.css · app.js（无构建步骤）
param-catalog.json
examples/demo_params.lomt
```

## 测试

```bash
cargo test --workspace
python ../../tools/loment_studio_test.py
```
