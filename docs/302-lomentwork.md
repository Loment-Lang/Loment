# 302 · Lomentwork：给"改 Loment 本身"的人的开发套件

> 状态：**已落地**（2026-11-04）
> 代码：`tools/loment_dev.py` · `.devcontainer/devcontainer.json` · `CONTRIBUTING.md`（§ The development kit）
> 判据：`tools/loment_dev_test.py`（7 条，注册在 `.github/gate-floors.txt`）
> 一句话：写 Loment 程序的一侧早就够了；**改 Loment 的一侧没有入口** —— 环境靠口口相传，
> 门禁命令靠读 `gate.yml` 猜，自举"卡住"与"跑着"分不出来。这三件事收成一条命令与一个容器。

---

## 0. 先盘清"缺的是什么"

说"没有开发套件"容易被理解成"缺能力"。不是。已经有的：

| 给谁 | 已有的 |
|---|---|
| 写 Loment 程序的人 | `loment` 38 个命令（`new`/`check`/`run`/`build`/`fmt`/`doc`/`lsp`/`doctor`…）· `lompi` 包管理 · VS Code 扩展与 LSP（商店 `hooyan2022.loment`）· `QUICKSTART.md` · `docs/164` 的源码包 |
| 读/改 Loment 的人 | `CONTRIBUTING.md` · `CLAUDE.md` / `AGENTS.md` · 门禁 `tools/ci.py` · 自举 `loment/bootstrap.sh` · `docs/158`（冻结面） |

缺的是**入口**：

1. **环境**：门禁跑在哪个镜像里、镜像里有什么、本机差哪一件 —— 只有读者自己去读
   `.github/ci/Dockerfile` 才知道。而"我这里过不了、CI 上过了"绝大多数就死在这上面。
2. **命令**：`python tools/ci.py --static-only` 之外还有 `-j`、`--only-static`、分片；
   哪些套件慢、慢在哪，不开跑看不出来。
3. **自举**：12–14 分钟没有输出，与"挂死了"长得一样。

这套东西只解决这三件，**不新增任何语言能力**。

## 1. 环境 = 门禁那枚镜像

门禁的每个 job 都跑在同一个容器里（`.github/workflows/gate.yml` 的 `container.image`），
镜像由 `.github/ci/Dockerfile` 构建：ubuntu 24.04 + **LLVM 19** + python3 + rustc/cargo +
go + jdk + node 20 + gh + gdb + qemu。它原本只是为了把"装工具链那 95s"挪进镜像构建，
副作用正好是我们要的东西：**一份可复现的贡献者环境**。

`.devcontainer/devcontainer.json` 直接用**同一枚摘要**：

```
ghcr.io/loment-lang/loment-ci@sha256:3053138c…e5c53
```

三件事已核过：

- **匿名可拉**（fork 的 PR 与外部贡献者没有凭据）：`ghcr.io/token` 拿到 token、
  `manifests/5` 回 `200`。包一旦被改成私有，外部贡献者的门禁会死在拉镜像那一步 ——
  那是 `ci-image.yml` 头注释里写过的单向门。
- **单平台**：返回的是单份 manifest，不是 index ⇒ 这份镜像**只有 amd64**。
  Apple Silicon 上要开模拟，速度与"是不是同一个环境"都会打折。
- **摘要与 tag 对得上**：`gate.yml` 的 `CI_IMAGE_DIGEST` / `CI_IMAGE_TAG` 与
  `ci-image.yml` 的 `TAG` 是同一版。抬 tag 时这三处 + `.devcontainer` 是四处一起改的。

`postCreateCommand` 做两件事：`git config --global --add safe.directory`（容器里 UID
与宿主不同时 git 会以 "dubious ownership" 拒绝 `ls-files`，门禁里好几条正靠它），
然后跑 `doctor` —— 缺东西在**进容器时**就说，而不是等跑门禁才现形。

## 2. 三条命令

```
python3 tools/loment_dev.py doctor      # 这棵检出能不能跑门禁；缺什么、去哪儿补
python3 tools/loment_dev.py gate        # = python3 tools/ci.py --static-only -j <核数>
python3 tools/loment_dev.py bootstrap   # = sh loment/bootstrap.sh（Windows 上走 WSL）
```

`doctor` 的每一条都带**怎么补**，而不是只说"有问题"：

```
  ok    python       3.12.3
  MISS  clang        找不到（自举与 genesis）
          fix: 装 clang：自举与 genesis；或直接用 .devcontainer（镜像里都有）
  ok    PYTHONUTF8   不适用（POSIX / 容器默认 UTF-8）
```

必检项（python / git / 检出完整性 / Windows 的 `PYTHONUTF8`）红了退 1；
可选工具（clang/rustc/go/javac/node/gh/gdb）红了只 warn —— 缺它们只会让**部分判据
SKIP**，不是进不了门。

`gate` 会在子进程里设 `PYTHONUTF8=1` 再跑。理由不是"统一环境"，是本机实测：Windows 上
不设它，子进程输出按 GBK 解码，判据会以 `NoneType is not iterable` 这种与真因无关的
样子假红（`loment_p8_test` 曾因此假报 14/29）。

`bootstrap` 在 Windows 上拼的是 `wsl -e bash -lc "cd d:/… && sh loment/bootstrap.sh"` ——
`sh.exe`（Git Bash）跑不了那份脚本，它要的是 Linux 上的 `sh`。

## 3. 三条坑（都实测过，写在命令里）

1. **`PYTHONUTF8=1`**：见上。
2. **自举走 WSL**：见上。
3. **12–14 分钟不是挂死**：本机 bootstrap 实测 12–14 分钟；`bootstrap` 子命令把预期
   写在开跑那一行，超时 30 分钟。

## 4. 判据钉的是"漂移"，不是"功能"

| 判据 | 钉住的漂移 |
|---|---|
| `test_doctor_is_green_on_a_complete_machine` | 全套工具在时体检必须是绿的（否则 doctor 只会报警，谁都不信它） |
| `test_doctor_names_the_missing_tool_and_the_fix` | 缺 clang 时只红 clang，且 fix 里点名要装什么 |
| `test_windows_without_pythonutf8_…` | 没设 `PYTHONUTF8` 是**必检红**；设了就绿 |
| `test_gate_command_is_the_one_in_the_docs` | `gate` 拼出的命令行与文档一致 |
| `test_bootstrap_goes_through_wsl_on_windows` | Windows 上必须 `wsl -e bash -lc` |
| `test_the_devcontainer_uses_the_same_image_as_the_gate` | **三方一致**：`.devcontainer` ≡ `gate.yml` 的 `CI_IMAGE_DIGEST` ≡ `ci-image.yml` 的 `TAG` |
| `test_the_contributor_guide_exists_and_is_linked` | 文档与 `CONTRIBUTING.md` 里的入口还在 |

第六条是这套东西里唯一一条"会随别人的改动变红"的判据，也是唯一值得随改动变红的一条：
抬镜像 tag 而漏改 `.devcontainer`，贡献者的环境就与 CI 悄悄分叉，而两边都是绿的。

## 5. 已知边界（别把没验过的说成验过的）

- **容器没有实跑过**：本机的 docker 引擎起不来，devcontainer 只做了静态核对
  （镜像可匿名拉取、摘要三方一致、JSON 合法）。"在 VS Code 里 Reopen in Container 能起来"
  这件事**尚未实测**。
- **arm64 未覆盖**：镜像只有 amd64。
- **本机与 CI 的 Python 版本仍可能不同**：`doctor` 会报出来（本机 3.11、CI 3.12），
  但不改它 —— 不替人升级解释器。
- **没有 `loment work new` 之类**：这一版刻意不碰 `loment` 命令行。它的两个实现
  （`loment/tools/lomcli.lomt` 与参考侧）要逐字节一致，加一个命令是双份的工；
  而 `tools/loment_dev.py` 只服务仓内贡献者，不需要进发行包的命令表。

## 6. 不做

- 不改门禁的判定（`gate-baseline.txt` 的基线、`gate-floors.txt` 的地板）—— 这套东西
  不许把"跑不动"洗成"通过"。
- 不把 Python 引进入内核构建链（`docs/165`）。
- 不新增语言能力，也不动 `lom/`。
