#!/usr/bin/env python3
# loment_zwframe_test.py — 零宽那一帧：**静态规则 + 它的证伪**（`docs/240`，`docs/218` §11）
#
# 判据（形状照 `tools/loment_admit_test.py` 那一对）：
#   1. 同一份用例，`loment/tools/lomzwframe.lomt`（链成 ELF 后跑）与 `tools/loment_zwframe.py`
#      的 **stdout / stderr / 退出码逐字节相同**；
#   2. **§11 自己的证伪**：零宽那一帧**不能用来传任意字节** ——
#      多一个字节 ⇒ `bad-length`；把载荷塞进保留位 ⇒ `reserved-nonzero`。
#      这两条就是 §11 那句"如果零宽流量最后能被用来传任意字节，这一轮作废"的可执行形式；
#   3. **接缝**：准入里 `w0` 的**字节预算 == 一个帧长**（`tools/loment_admit.py` 的 `WIDTH`）。
#      两边各写一个数就会漂：一边说"只够一次验证交换"、另一边说"能给 64 KiB"。
#
# 运行: python tools/loment_zwframe_test.py   (无 clang/WSL 时 SKIP, 退出码 0)

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import loment_suite  # noqa: E402
import loment_admit  # noqa: E402
import loment_zwframe as Z  # noqa: E402
import lomentc  # noqa: E402

_T = f"/tmp/loment-zwf-{os.getpid()}-"
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "loment" / "tools" / "lomzwframe.lomt"
TESTS: list[tuple[str, object]] = []


def test(fn):
    TESTS.append((fn.__name__, fn))
    return fn


H32 = "a" * 64
D32 = "b" * 64


def _corpus() -> str:
    """用例：`make` 两种种类各来一条，`parse` 用**真帧**（拿参考实现产的，不手搓 hex）。"""
    f1 = Z.make_frame(1, bytes.fromhex(H32), bytes.fromhex(D32))
    f2 = Z.make_frame(2, bytes.fromhex(H32), bytes.fromhex(D32))
    # **证伪 1**：真帧后面挂一个字节的载荷 —— 不许被当成"多出来的部分忽略掉"
    with_tail = f1 + b"X"
    # **证伪 2**：把载荷塞进保留位 —— 保留位必须全 0
    with_resv = f1[:5] + bytes([1, 2, 3]) + f1[8:]
    bad_magic = b"XXXX" + f1[4:]
    bad_kind = f1[:4] + bytes([9]) + f1[5:]
    lines = [
        f"make 1 {H32} {D32}",
        f"make 2 {H32} {D32}",
        f"parse {f1.hex()}",
        f"parse {f2.hex()}",
        f"parse {with_tail.hex()}",
        f"parse {with_resv.hex()}",
        f"parse {bad_magic.hex()}",
        f"parse {bad_kind.hex()}",
        # 截断到 71 字节（少一个字节也是长度不对）
        f"parse {f1[:-1].hex()}",
        # 十六进制本身不合法 ⇒ parse 那侧是**一条拒绝**（不是"用例不成形"）
        "parse zz",
        "parse abc",
    ]
    return "".join(x + "\n" for x in lines)


def _clang() -> str | None:
    p = shutil.which("clang")
    if p:
        return p
    fallback = r"C:\Program Files\LLVM\bin\clang.exe"
    return fallback if Path(fallback).exists() else None


#: `#371`: 能不能跑 Linux ELF 取决于**平台** —— Linux 上原生跑, Windows 上经 WSL。
#: 写法上是个**前缀**（`[*_WSLCMD, "bash", "-lc", …]`），所以各调用点不必知道细节。
_WSLCMD = [] if sys.platform.startswith("linux") else ["wsl", "-e"]


def _wsl() -> bool:
    if sys.platform.startswith("linux"):
        return True
    if not shutil.which("wsl"):
        return False
    try:
        return subprocess.run([*_WSLCMD, "true"], capture_output=True,
                              text=True, timeout=60, shell=False).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    if sys.platform.startswith("linux"):
        return s
    return "/mnt/" + s[0].lower() + s[2:]


def _build(td: Path) -> Path:
    mod = lomentc.load(SRC)
    deps = lomentc.resolve_deps(mod, ROOT, SRC.parent, entry=SRC)
    errs = lomentc.check(mod, deps=deps)
    assert not errs, f"lomzwframe.lomt 自己检查不过: {errs[:2]}"
    ll = td / "lomzwframe.ll"
    with ll.open("w", encoding="utf-8", newline="\n") as f:
        f.write(lomentc.emit_llvm(mod, ROOT, deps))
    elf = td / "lomzwframe.elf"
    r = subprocess.run(
        [_clang(), "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
         "-static", "-fuse-ld=lld", "-o", str(elf), str(ll)],
        capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stderr[-400:]
    return elf


def _py(args: list[str]) -> tuple[int, str, str]:
    buf, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = Z.main(args)
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 1
    return rc, buf.getvalue(), err.getvalue()


def _run(elf: Path, td: Path, name: str, args: list[str]) -> tuple[int, str, str]:
    outp, errp = td / f"{name}.out", td / f"{name}.err"
    binn = f"{_T}zwf_{name}.bin"
    script = (f"rm -f {binn} && cp {_wsl_path(elf)} {binn} && chmod +x {binn} && "
              f"cd {_wsl_path(ROOT)} && {binn} {' '.join(args)} "
              f"> {_wsl_path(outp)} 2> {_wsl_path(errp)}; echo -n $?")
    r = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                       capture_output=True, text=True, timeout=300, shell=False)
    out = outp.read_text(encoding="utf-8") if outp.exists() else ""
    err = errp.read_text(encoding="utf-8") if errp.exists() else ""
    try:
        rc = int(r.stdout.strip())
    except ValueError:
        rc = -1
    return rc, out, err


def _pair(elf: Path, td: Path, name: str, args: list[str],
          wsl_args: list[str]) -> tuple[int, str, str]:
    """两侧比 **stdout / stderr / 退出码** —— 退出码也要比（只比输出会漏掉"该退 1 的退了 0"）。"""
    wr, wo, we = _py(args)
    gr, go, ge = _run(elf, td, name, wsl_args)
    if (wr, wo, we) == (gr, go, ge):
        return gr, go, ge
    raise AssertionError(
        f"[{name}] 不一致: rc py={wr} loment={gr}\n  py     out={wo[-400:]!r} err={we[-200:]!r}\n"
        f"  loment out={go[-400:]!r} err={ge[-200:]!r}")


def _ready() -> bool:
    if not (_clang() and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return False
    return True


# ---------------------------------------------------------------- 判据

@test
def test_the_corpus_matches_python_and_the_frame_cannot_carry_payload():
    """两侧逐字节相同；而且**证伪那两条必须在语料里真的出现**。

    只比"两边一样"不够 —— 两份都放行"多一个字节的帧"也会一样。所以这里同时钉住
    结论本身：真帧 `ok`、挂了载荷的 `bad-length`、塞了保留位的 `reserved-nonzero`。
    """
    if not _ready():
        return
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        elf = _build(td)
        cf = td / "cases.txt"
        cf.write_text(_corpus(), encoding="utf-8", newline="\n")
        rc, out, err = _pair(elf, td, "corpus", [str(cf)], [_wsl_path(cf)])
        assert rc == 0, (rc, out[-300:], err[-300:])
        ok = Z.make_frame(1, bytes.fromhex(H32), bytes.fromhex(D32))
        assert out.count(f"frame={ok.hex()}") == 1, out[:200]
        outs = out.splitlines()
        assert outs[2] == "ok kind=1 len=72", outs[:4]
        assert outs[4] == "reject why=bad-length", outs[4]
        assert outs[5] == "reject why=reserved-nonzero", outs[5]
        assert outs[6] == "reject why=bad-magic", outs[6]
        assert outs[7] == "reject why=bad-kind", outs[7]
        assert outs[8] == "reject why=bad-length", outs[8]
        assert outs[9] == "reject why=bad-hex" and outs[10] == "reject why=bad-hex", outs[9:]


@test
def test_the_frame_is_fixed_size_with_no_length_or_payload_field():
    """**规则本身**（不依赖另一份实现）：定长、两个定长摘要、保留位必须全 0。

    这条是给读代码的人看的 —— "这一帧装不下别的东西"在这几行里就是全部理由。
    """
    h, d = bytes.fromhex(H32), bytes.fromhex(D32)
    fr = Z.make_frame(1, h, d)
    assert len(fr) == Z.FRAME_LEN == 72, len(fr)
    assert fr[:4] == Z.MAGIC and fr[4] == 1 and fr[8:40] == h and fr[40:72] == d, fr.hex()
    # 帧的**每一格**都有名字与固定宽度；没有一格是"长度"或"载荷"
    assert Z.HASH_LEN == 32 and Z.DIGEST_LEN == 32 and Z.RESERVED_LEN == 3
    assert 4 + 1 + 3 + Z.HASH_LEN + Z.DIGEST_LEN == Z.FRAME_LEN
    # 种类**只有两个**（多一个种类就是多一条通道）
    assert tuple(Z.KINDS) == (1, 2), Z.KINDS
    # 长度多一字节、少一字节、保留位非零，逐条拒（顺序也钉住：长度先看）
    assert Z.parse_frame(fr + b"\x00") == ("reject", {"why": Z.BAD_LENGTH})
    assert Z.parse_frame(fr[:-1]) == ("reject", {"why": Z.BAD_LENGTH})
    assert Z.parse_frame(fr[:5] + b"\x00\x00\x01" + fr[8:])[1]["why"] == Z.RESERVED_NONZERO


@test
def test_w0_byte_budget_is_exactly_one_frame():
    """**接缝**：准入里 `w0` 的字节预算 == 一个帧长（`docs/240` §3）。

    两个数分开写就会漂：一个说"只够一次验证交换"、另一个说"能给 64 KiB" ——
    而那条"只够验证对端字节"的话正是 §11 的立足点。
    """
    conns, nbytes, life = loment_admit.WIDTH["w0"]
    assert conns == 0, conns
    assert nbytes == Z.FRAME_LEN, (nbytes, Z.FRAME_LEN)
    assert life == 1, life


@test
def test_lomzwframe_selfhost_compiles():
    """`lomzwframe.lomt` 也要走**种子自举链**编得出来（形参 ≤ 10、入口 `die(0)` 那两处坑）。"""
    clang = _clang()
    if not (clang and _wsl()):
        print("      SKIP: 无 clang/WSL")
        return
    seed = ROOT / "loment" / "build" / "selfhost_driver.ll"
    assert seed.exists(), "缺自举种子"
    with tempfile.TemporaryDirectory() as tds:
        td = Path(tds)
        s1 = td / "stage1"
        r = subprocess.run(
            [clang, "--target=x86_64-unknown-linux-gnu", "-nostdlib", "-ffreestanding",
             "-static", "-fuse-ld=lld", "-o", str(s1), str(seed)],
            capture_output=True, text=True, shell=False)
        assert r.returncode == 0, r.stderr[-300:]
        binn = f"{_T}zwf_s1.bin"
        script = (f"cp {_wsl_path(s1)} {binn} && chmod +x {binn} && "
                  f"cd {_wsl_path(ROOT)} && {binn} loment/tools/lomzwframe.lomt")
        rr = subprocess.run([*_WSLCMD, "bash", "-lc", script],
                            capture_output=True, timeout=600, shell=False)
        assert rr.returncode == 0, f"stage1 编译 lomzwframe.lomt 失败: {rr.stderr[-300:]}"
        assert len(rr.stdout) > 2000, f"产物太小 ({len(rr.stdout)}B)"
    print(f"      种子自举链编译 lomzwframe.lomt 成功 ({len(rr.stdout)}B IR)")


def main() -> int:
    return loment_suite.run(TESTS, "loment_zwframe_test")


if __name__ == "__main__":
    raise SystemExit(main())
