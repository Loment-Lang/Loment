# 门禁的 CI 工具链 —— **只在这里装一次**，各 job 用 `container:` 拉它。
#
# ## 为什么要它（docs/213 §7）
#
# 装工具链那 95s（apt 55s + LLVM 19 35s）原来是**每个分片各付一次** —— 而它是"同一份
# 东西装 N 遍"。做成镜像之后，那 95s 只在**镜像构建时**付一次（且只在下面这份文件改动
# 时），各分片只剩拉镜像。顺带把"apt 在镜像上抽风"的暴露面也一起消掉：抽风的那一次
# 发生在构建里，不占任何人的墙钟。
#
# ## 包名单从哪来
#
# **不是新设计的**，是原样搬 `gate.yml` 里那两条（apt + `llvm.sh`），再加上原来**由
# runner 镜像提供、而容器里没有**的那几件（`git` / `python3` / `gh` / `make` / 解压工具）。
# 那第二类的依据是**逐条扫判据**：`grep -rn 'which("' tools/*.py` 给出各判据"缺了就跳过"
# 的命令名，与这份名单对照。
#
# **不许随手增删**：少一个包就是一条**假红**，多一个包是白等的下载。两条纪律：
#   * **不加** `dotnet` / `nvim` —— runner 上本来就没有，那几条判据靠"缺"来 SKIP，
#     而**那些 SKIP 已经写进基线**（`loment_multisyntax_projects_test` 那条"runner 上
#     2/3"是其中一个）。加进来会改变覆盖面，不是改进。
#     **`rustc`/`cargo` 不在"不加"之列 —— 这一条改过一次**：最初按"原 gate.yml 那两条
#     apt 里没有 rustc"推断 runner 也没有，于是故意不加；换上容器后 `loment_tools_test`
#     的 `test_m61_test_framework` / `test_m62_bench_table` 立刻报
#     `FileNotFoundError: 'rustc'`（它们**裸调** `rustc`，没有 which 守卫）。查回旧 runner
#     的自述才看清：`rustc  /home/runner/.cargo/bin/rustc` —— **runner 自带**（rustup 装的），
#     只是从来没写进 gate.yml。所以这里加 `rustc cargo`（apt 版）把它补回来。
#   * 增删之后要跑一遍门禁，看 `环境自述` 那一步的输出与红项有没有变。
#
# **`gcc g++ libc6-dev zlib1g-dev python3-dev`（2026-10-10 加，随 `docs/222` 的
# `--hosted`）**：这是上面那条纪律的一次**正面使用** —— 不是"补一个本来就该有的"，
# 是**新能力要的新依赖**。
#
# 原来一条都不需要：`--opt` 走的是 `clang -nostdlib -ffreestanding -static`，刻意不碰
# libc。而 `--hosted` 要的正是反过来 —— **链真 libc、链真库**：
#   * `libc6-dev` —— `crt1.o` / `libc.so`（没有它连一个普通 C 程序都链不出来）；
#   * `zlib1g-dev` —— `libz.so` 那个符号链接（运行期的 `libz.so.1` 不足以 `-lz`）；
#   * `g++` —— C++ 那条判据要按**改名字符**调一个真类，链接要 `libstdc++`；
#   * `python3-dev` —— `libpython3.x.so` 那个符号链接，嵌 CPython 要它。
#
# Java 那条**不需要新包**：`default-jdk` 本来就在名单里，它同时提供 `jni.h` 与
# `libjvm.so`。
#
# 判据是 `loment_hosted_test` 的四个生态那几条：缺哪个包，**哪一条红**（不是静默
# 少跑一条）。这正是要的效果 —— 少一个包就是一条假红。
# 基础镜像**按 digest 钉住** (#475): `ubuntu:24.04` 是可变 tag, 上游重新推送就能换掉
# 整张镜像。digest 是 `docker pull ubuntu:24.04` 时拿到的 manifest list 摘要。
FROM ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852
ENV DEBIAN_FRONTEND=noninteractive

# 这一组与原来 `gate.yml` 那一步**逐字对应**（`qemu-system-x86 qemu-user golang-go
# default-jdk nodejs npm vim`），后面几件是 runner 镜像本来就有、容器里得自己装的。
# 不用 `--no-install-recommends`：原来那一步也没用，换掉会静默改环境。
#
# **`lsb-release` + `software-properties-common` + `gnupg` + `wget` 那四件是给 `llvm.sh`
# 用的**（它在 runner 上跑时那四件都在，所以以前没人注意）。少一个就是一次构建失败 ——
# 2026-10-10 连着红了两轮：先 `lsb_release: command not found`，再
# `Missing required tools: add-apt-repository`（`software-properties-common` 提供）。
# `llvm.sh` 自己的提示原话就是 `apt install lsb-release wget software-properties-common gnupg`。
# **`python-is-python3` 是补 runner 与容器的差**：runner 镜像上有 `python`（指向 3），
# 而 ubuntu 24.04 只有 `python3`。`vscode_ext_test` 的 `_python()` 是
# `shutil.which("python") or "python"` —— 找得到就用，找不到**退回裸名**，于是容器里
# 直接 `FileNotFoundError: 'python'`（2026-10-10 实测，那一条因此从绿变红）。
RUN apt-get update -qq && apt-get install -y -qq \
      ca-certificates curl wget gnupg git sudo lsb-release software-properties-common \
      python3 python-is-python3 make unzip xz-utils diffutils \
      rustc cargo \
      gcc g++ libc6-dev zlib1g-dev python3-dev \
      qemu-system-x86 qemu-user \
      golang-go default-jdk \
      nodejs npm vim \
    && rm -rf /var/lib/apt/lists/*

# `gh`：`loment_publish` 用它（runner 镜像自带，容器里没有）。
# keyring 是 apt 的信任根 —— 下载后先核 **固定 sha256** 再装进 keyring 目录 (#475)。
RUN mkdir -p -m 755 /etc/apt/keyrings \
    && wget -qO /tmp/githubcli-archive-keyring.gpg \
         https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    && echo "6084d5d7bd8e288441e0e94fc6275570895da18e6751f70f057485dc2d1a811b  /tmp/githubcli-archive-keyring.gpg" | sha256sum -c - \
    && mv /tmp/githubcli-archive-keyring.gpg /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
         > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update -qq && apt-get install -y -qq gh \
    && rm -rf /var/lib/apt/lists/*

# gdb：`diagnose_twin` 那个定向探针用它（原来是探针步骤里现装）。
RUN apt-get update -qq && apt-get install -y -qq gdb && rm -rf /var/lib/apt/lists/*

# Node 20（**固定版本**，官方 tarball）盖在 apt 那个 18 上面（`/usr/local/bin` 在
# `/usr/bin` 前面）。两条理由：
#   * **runner 上就是 20**（GitHub 的镜像自带），而 ubuntu 24.04 的 `nodejs` 是 18 ——
#     判据 `vscode_ext_test` 拿它跑 JS，版本对齐更贴近原来的环境；
#   * **`container:` job 里的 JS action**（`actions/checkout`、`upload-artifact@v4` 都是）
#     `using: node20`。它们用的是 runner 挂进来的 node 还是镜像里的，取决于 runner 怎么
#     挂载 —— 猜不如给一个 20 放在 PATH 最前面。
# 用固定 tarball 而不是"下载一个 setup 脚本再执行"：前者可复现（`llvm.sh` 那一步是
# apt.llvm.org 官方给的唯一路子，两回事）。
RUN arch=$(dpkg --print-architecture); \
    case "$arch" in \
      amd64) na=x64; nsha=b4e336584d62abefad31baecff7af167268be9bb7dd11f1297112e6eed3ca0d5 ;; \
      arm64) na=arm64; nsha=dbe339e55eb393955a213e6b872066880bb9feceaa494f4d44c7aac205ec2ab9 ;; \
      *) echo "不认识的架构 $arch" >&2; exit 1 ;; esac; \
    wget -qO /tmp/node.tar.xz "https://nodejs.org/dist/v20.19.0/node-v20.19.0-linux-$na.tar.xz" \
    && echo "$nsha  /tmp/node.tar.xz" | sha256sum -c - \
    && tar -xJf /tmp/node.tar.xz -C /opt \
    && mv "/opt/node-v20.19.0-linux-$na" /opt/node \
    && for b in node npm npx; do ln -sf "/opt/node/bin/$b" "/usr/local/bin/$b"; done \
    && rm -f /tmp/node.tar.xz \
    && /usr/local/bin/node -v

# LLVM 19 + 不带版本号的符号链接 —— 与原来 `gate.yml` 里那几行**同一套**。
# 为什么非要 19：仓里发出的 IR 用 `#dbg_declare`（LLVM 19 起的调试记录语法），而 ubuntu
# 自带的 clang 是 18 → `--debug` 那几条判据会报 `error: expected instruction opcode`。
# `llvm.sh` 是**下载后执行**的远端脚本 —— 钉住它的 sha256 (#475)。上游一改, 这里就
# 构建失败, 逼一次 review 再抬钉子, 而不是让新脚本悄悄以 root 跑进门禁的信任根。
RUN wget -qO /tmp/llvm.sh https://apt.llvm.org/llvm.sh \
    && echo "03878e08f47b66cc95bc4b544b0db3c6d9ce8d60e6cf2492ae357984330a9eae  /tmp/llvm.sh" | sha256sum -c - \
    && chmod +x /tmp/llvm.sh && /tmp/llvm.sh 19 \
    && for t in clang clang++ lld ld.lld llvm-objdump llvm-nm llvm-ar; do \
         if [ -x "/usr/lib/llvm-19/bin/$t" ]; then ln -sf "/usr/lib/llvm-19/bin/$t" "/usr/local/bin/$t"; fi; \
       done \
    && rm -f /tmp/llvm.sh && rm -rf /var/lib/apt/lists/*

# 自述一遍 —— 构建日志里就能看出这一版镜像装成了什么（门禁那边 `环境自述` 是另一半）。
# **`set -e` 加上这几行本身就是一条判据**：哪个包被删了，构建就在这里失败，而不是等到
# 门禁上变成一条"看着像判据有问题"的假红。
RUN set -e; \
    python3 -V; python --version; git --version; make --version | head -1; \
    clang --version | head -1; llvm-objdump --version | head -1; \
    qemu-system-x86_64 --version | head -1; qemu-aarch64 --version | head -1; \
    go version; javac -version; node -v; gh --version | head -1; gdb --version | head -1; \
    rustc -V; cargo -V
