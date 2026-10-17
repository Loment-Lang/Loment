# 255 · 仓库群搬进 `Loment-Lang` 组织

> 状态: **已落地（2026-10-16/17）** · 落点: 三个库的归属、`Loment-Lang/.github`、
> `tools/loment_publish.py` 的 `ORG`、`.github/workflows/gate.yml` 的镜像路径
> · 号取 255：254 被同一轮的 CI 改动（`docs/254`）占了
>
> 一句话: **搬完要按顺序修三件东西** —— 站点 URL、门禁拉的那个镜像、以及 Actions secret；
> 其中镜像那一件**顺序错了会让每个 PR 的门禁都拉不到镜像**，而它有一个先有鸡还是先有蛋。

---

## 1. 搬了什么

| 从 | 到 |
|---|---|
| `FujoJTOP/loment` | `Loment-Lang/loment` |
| `FujoJTOP/lompi` | `Loment-Lang/lompi` |
| `FujoJTOP/loment-site` | `Loment-Lang/loment-site` |

另建 `Loment-Lang/.github`（组织主页 `profile/README.md`），并填了组织元数据
（name `Loment`、介绍、网站）。三个库与组织主页都设了 topics，三个库的 homepage 都指向站点。

**跟着走的东西**（核实过）：`main` 的分支保护（`enforce_admins: false` + 两条必需检查）、
`loment-site` 的 Pages 构建、打开的 PR/issue（编号不变）。**旧仓 URL 会 301**：
`github.com/FujoJTOP/loment` → `github.com/Loment-Lang/loment`，所以 `git` 的
`origin`、以及代码注释里那几百处 `FujoJTOP/loment#NNN` **都不用动**，它们照样解析。

**旧站点的 URL 不会 301。** `fujojtop.github.io/loment-site/` 现在是 **404**，
新地址是 `loment-lang.github.io/loment-site/`（组织 Pages 用组织名做子域）。
所以所有**活链接**都改了：`README.md`、`QUICKSTART.md`、`tools/loment_publish.py`
（两处 `homepage` 字面量 + README 模板）、`.github/ISSUE_TEMPLATE/*`。

**没动的东西**：`FujoJTOP/loment#NNN` 那类**历史引用** —— 它们指向某个具体 issue，
301 让它们照样解析，改写只是噪音。改的是"读的人会点的导航"，不是"当时发生在那儿的凭据"。

---

## 2. 那个先有鸡还是先有蛋（照这个顺序做）

门禁各片用 `container: image: ghcr.io/<owner>/loment-ci:<tag>` 拉工具链镜像
（`docs/213`：把 95s 的装工具链从每次跑挪到镜像构建）。而**镜像路径里带着属主名**：

* `gate.yml` 里是**硬编码**的 `ghcr.io/fujojtop/loment-ci:5`；
* `ci-image.yml` 是**算出来的** —— `github.repository_owner` 转小写，现在会算出
  `ghcr.io/loment-lang/loment-ci`。

于是**顺序是硬的**：

1. 先跑一次 `ci-image.yml`（`workflow_dispatch`），让新属主下**存在**那个 tag；
2. 把新包设成 **public** —— 包默认私有，而**从 fork 提的 PR 拿不到私有包**，它们会在
   "拉镜像"这一步就死（`ci-image.yml` 文件头写着这一条，第一次就是这么红的）；
3. **这时才**改 `gate.yml` 里那两行。

顺序反了（先改 `gate.yml`）→ 每个 PR 的每一片都拉不到镜像 → 门禁全红，
而红的样子是"容器起不来"，不是一条判据在说话。

旧属主下的那个包**还在**（`fujojtop/loment-ci`，public），所以第 1 步之前门禁照常跑 ——
搬迁不会有一个"门禁全断"的窗口。

**第 3 步现在有判据看着了。** `loment_tools_test::test_gate_container_image_matches_the_recipe`
原来只比 tag，属主那一半**没人管** —— 而属主正是这条路上会分叉的那一半
（`gate.yml` 写死、`ci-image.yml` 用 `github.repository_owner` 算）。判据改成比
**属主 + tag**，期望的属主取自 `loment_publish.ORG` 转小写。它在改之前是**红的**
（库已经住进 `Loment-Lang`，`gate.yml` 还指着 `fujojtop`），改完才绿。

**怎么判"到底公没公开"**：裸 `curl` 打 `/v2/.../manifests/5` **永远 401**（那是缺 bearer
token，不是私有），得先匿名要一次 token：
`curl -s "https://ghcr.io/token?service=ghcr.io&scope=repository:<owner>/loment-ci:pull"`
—— 私有包拿不到 token。2026-10-17 实测：`fujojtop` 拿得到（public）、`loment-lang`
拿不到（当时 private），翻过之后拿得到 ✓。

---

## 3. 搬家不带过来的东西

* **Actions secrets 不会跟过来。** `secrets.MANIFEST_TOKEN`（`docs/254` §3.2 那个
  main 侧的写者要用的属主 PAT）要在**新仓**上重建。
* Actions 的**运行历史与缓存**不搬（无所谓）。
* 仓的设置里 `default_workflow_permissions` 是 `read`；`ci-image.yml` 自己声明了
  `permissions: packages: write`，构建照常（核实于 2026-10-17 那次构建）。

---

## 4. 顺带看见的另一个缺陷（**没在这一轮修**）

`tools/loment_publish.py` 里那份 `"readme"` 模板，与仓库根上真正的 `README.md`
**已经显著漂了**（模板 6331 字符 / README 3609 字符，147 行差异）—— 而 `--push`
是**拿模板覆盖那边 README** 的（`cmd_push` 里 `write_text(spec["readme"])`）。
两件事都不对：模板停在旧写法，而"README 由工具直出"这句话在两边不一致时就是假的。
这一轮只把**两个文件里的 URL** 都改到新组织，没有把文本对齐 —— 那是独立的一笔。
