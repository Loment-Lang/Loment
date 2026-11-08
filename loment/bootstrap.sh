#!/bin/sh
# loment/bootstrap.sh — 重建 Loment 编译器 (无 Python, 无解释器, 且**不需要 clang**)
#
# 链条的起点是 **genesis**: loment/build/genesis/lomelf-linux-x64.elf —— 由种子构建出来、
# 提交进仓库的 `lomelf` (docs/167 §5 ②)。它是**汇编器**: 把 `.ll` 变成可执行文件。
# 有了它, 整条链上没有任何 C 编译器, 也没有任何解释器:
#
#   1. genesis(种子) -> stage1
#   2. stage1 编译 driver.lomt -> 必须与种子逐字节相同 (种子自身就是个定点)
#   3. genesis(s2.ll) -> stage2; stage2 编译同一入口 -> 与 s2.ll 相同 (三阶段定点)
#   4. stage1 与 stage2 对**非自身**入口 (native_res) 的产物相同
#
# 用法:
#   sh loment/bootstrap.sh              # 跑完 1-4 (几十秒)
#   sh loment/bootstrap.sh ENTRY.lomt   # 追加: 用 stage1 把该入口的 IR 打到 stdout
#
# 依赖: POSIX sh + cmp。**没有 genesis 时**退回 clang (老路子, 见 docs/159);
# 设 `LOMENT_USE_CLANG=1` 可以强制走 clang 那条, 用来对照。
#
# 两种运行环境:
#   * Linux/macOS/WSL: 直接用 genesis (或本机 clang), 工作目录在 mktemp -d 里;
#   * WSL 里没有 genesis 也没有 clang、但 Windows 侧有 LLVM: 用 /mnt/c/... 的 clang.exe
#     (互操作), 此时输入/输出必须是 Windows 看得见的路径 (wslpath -w 转换), 而**执行** ELF
#     仍要拷到 /tmp (DrvFs 上不能直接跑), 所以工作目录落在 loment/build/。
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"

seed=loment/build/selfhost_driver.ll
genesis=loment/build/genesis/lomelf-linux-x64.elf
entry=loment/selfhost/driver.lomt
target=x86_64-unknown-linux-gnu
cross=0

if [ ! -f "$seed" ]; then
    echo "FAIL: 缺种子 $seed (用 python tools/loment_seed.py --emit 固化)" >&2
    exit 1
fi

# ---- 起点: genesis (无 clang) 优先
if [ -x "$genesis" ] && [ "${LOMENT_USE_CLANG:-0}" != 1 ]; then
    # genesis 是**可执行代码**, 且整条链的根就在它身上 —— 执行前先核对
    # `SHA256SUMS` (#437)。同目录的哈希只防"拿错了文件", 不防恶意替换; 但
    # "什么都不查就执行"连前者都没有。真正证明它等于源码产物的是
    # `tools/loment_genesis.py --check/--verify` 与门禁里那条重建判据。
    sums=loment/build/genesis/SHA256SUMS
    if [ ! -f "$sums" ]; then
        echo "FAIL: 缺 $sums —— 无法校验 genesis, 拒绝执行" >&2
        exit 1
    fi
    want=$(head -n 1 "$sums" | cut -d' ' -f1)
    got=
    if command -v sha256sum >/dev/null 2>&1; then
        got=$(sha256sum "$genesis" | cut -d' ' -f1)
    elif command -v shasum >/dev/null 2>&1; then
        got=$(shasum -a 256 "$genesis" | cut -d' ' -f1)
    elif command -v openssl >/dev/null 2>&1; then
        got=$(openssl dgst -sha256 -r "$genesis" | cut -d' ' -f1)
    fi
    if [ -z "$got" ] && [ "${LOMENT_ALLOW_UNVERIFIED_GENESIS:-0}" != 1 ]; then
        echo "FAIL: 没有 sha256 工具 (sha256sum / shasum / openssl) 可校验 genesis" >&2
        echo "      set LOMENT_ALLOW_UNVERIFIED_GENESIS=1 to run it unverified" >&2
        exit 1
    fi
    if [ -n "$got" ] && [ "$got" != "$want" ]; then
        echo "FAIL: genesis 的 sha256 与 SHA256SUMS 不符 —— 拒绝执行" >&2
        echo "      want $want" >&2
        echo "      got  $got" >&2
        exit 1
    fi
    if [ -n "$got" ]; then
        echo "== genesis sha256 verified (${got})"
    else
        echo "== WARNING: genesis NOT verified (LOMENT_ALLOW_UNVERIFIED_GENESIS=1)"
    fi
    work=$(mktemp -d)
    trap 'rm -rf "$work"' EXIT
    link() {   # link IN.ll OUT.bin
        "$root/$genesis" "$1" "$2"
    }
    drive() {  # drive ELF ENTRY -> stdout
        "$1" "$2"
    }
    echo "== 起点: genesis ($(basename "$genesis")) —— 无 clang, 无解释器"
else
    cc=${CC:-}
    if [ -z "$cc" ]; then
        if command -v clang >/dev/null 2>&1; then
            cc=clang
        elif [ -x "/mnt/c/Program Files/LLVM/bin/clang.exe" ]; then
            cc="/mnt/c/Program Files/LLVM/bin/clang.exe"
        else
            echo "FAIL: 既没有 genesis 也没有 clang (可用 CC=/path/to/clang 指定)" >&2
            exit 1
        fi
    fi
    case "$cc" in
        /mnt/*) cross=1 ;;
    esac

    # Windows 版 clang 不认 /mnt/... 参数, 要转成 D:\... (固定仓库内路径, 无用户输入;
    # 需要 wslpath, WSL 自带)。
    win() {
        if [ "$cross" = 1 ]; then
            wslpath -w "$1"
        else
            printf '%s' "$1"
        fi
    }

    if [ "$cross" = 1 ]; then
        work="$root/loment/build/seed-work.$$"
        mkdir -p "$work"
    else
        work=$(mktemp -d)
    fi
    trap 'rm -rf "$work"' EXIT

    link() {   # link IN.ll OUT.bin
        "$cc" --target="$target" -nostdlib -ffreestanding -static -fuse-ld=lld \
              -o "$(win "$2")" "$(win "$1")"
    }

    drive() {  # drive ELF ENTRY -> stdout
        exe="$1"
        if [ "$cross" = 1 ]; then
            # DrvFs 上不能直接执行: 拷进 /tmp 再跑 (LD 之类的路径无关, 驱动只读入口文件)
            cp "$1" "/tmp/loment_seed_run.$$"
            chmod +x "/tmp/loment_seed_run.$$"
            exe="/tmp/loment_seed_run.$$"
        fi
        "$exe" "$2"
    }
    echo "== 起点: clang ($(basename "$cc")) —— 没有 genesis, 退回老路子"
fi

echo "== 1/4 种子 -> stage1"
link "$seed" "$work/stage1"
chmod +x "$work/stage1"

echo "== 2/4 stage1 编译 $entry -> 必须等于种子"
drive "$work/stage1" "$entry" > "$work/s2.ll"
if ! cmp -s "$work/s2.ll" "$seed"; then
    echo "FAIL: stage1 的产物与种子不一致 (种子过期? 见 docs/159)" >&2
    exit 1
fi

echo "== 3/4 stage2 -> stage3 定点"
link "$work/s2.ll" "$work/stage2"
chmod +x "$work/stage2"
drive "$work/stage2" "$entry" > "$work/s3.ll"
if ! cmp -s "$work/s3.ll" "$work/s2.ll"; then
    echo "FAIL: 第 3 阶段与第 2 阶段不一致 (未定点)" >&2
    exit 1
fi

echo "== 4/4 stage1 与 stage2 对非自身入口一致"
probe=loment/examples/native_res.lomt
drive "$work/stage1" "$probe" > "$work/p1.ll"
drive "$work/stage2" "$probe" > "$work/p2.ll"
if ! cmp -s "$work/p1.ll" "$work/p2.ll"; then
    echo "FAIL: stage1/stage2 对 $probe 的产物不一致" >&2
    exit 1
fi

if [ "$#" -ge 1 ]; then
    echo "== 发射 $1 (stage1)" >&2
    drive "$work/stage1" "$1"
    exit 0
fi

wc -c "$seed" "$work/p1.ll" | sed 's/^/   /'
echo "SEED BOOTSTRAP OK: 无 Python, 无解释器, 无 clang (genesis 起头); 种子自复现 + 三阶段定点"
