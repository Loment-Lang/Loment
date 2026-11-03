#!/usr/bin/env python3
# loment_demo_release.py — 自己搭一个"GitHub Pre-Release"，然后看 Loment 会不会换。
#
# **不在用户的仓库里创建任何东西**：所有产物都在 `--work` 指定的临时目录里。
#
#   1. `make`  造两棵源码树（基线与"投影后"）、把工具编成 hotswap 档、
#              打出 `candidate.hot` + `hotfix.json` + `SHA256SUMS`，用一次性密钥签名 ——
#              这一份目录就是 GitHub Pre-Release 的 asset 列表。
#   2. `stage` 用 `loment_staging.fetch` 走真准入：签名、五个等式、预发布、单调，
#              然后把候选放进 `$LOMENT_HOT_HOME/<baseline>/incoming/<tag>/`。
#   3. `run`   跑**基线**那个二进制；它在安全点读暂存目录里那一份候选，交给固定核。
#
# 传输在进程外，换的那条路只在本地读一个文件 —— 与 `docs/286` 的分工一致。

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import loment_hotart as ha        # noqa: E402
import loment_hotfix as hf        # noqa: E402
import loment_staging as st       # noqa: E402
import loment_update as lu        # noqa: E402

_COPY = (
    "lom/*.lom",
    "tools/loment_release.py",
    "loment/tools/*.lomt",
    "loment/selfhost/*.lomt",
    "tools/lomentc.py",
    "tools/lomelf.py",
    "tools/lomfmt.py",
    "tools/loment.py",
    "loment/hotswap/fixed.txt",
    "loment/lib/*.lomt",
    "loment/examples/bytes.lomt",
)

DEMO = """module demotool
choose runtime
use bytes
use hotswap

const VERSION: u32 = {version};

fn demo_wa(fd: u64, p: ptr, n: u64) -> i64 {{
    let off: u64 = 0;
    while off < n {{
        let w: i64 = syscall4(1, fd, ptr_add(p, off as u32) as u64, n - off);
        if w <= 0 {{
            return w;
        }}
        off = off + w as u64;
    }}
    return off as i64;
}}

fn demo_ws(fd: u64, s: str) -> i64 {{
    return syscall4(1, fd, str_ptr(s) as u64, str_len(s) as u64);
}}

fn demo_wd(fd: u64, v: u32) -> i64 {{
    let b: ptr = alloc(64);
    let x: u32 = v;
    let d: u32 = 0;
    if x == 0 {{
        store8(b, 0, 48 as u8);
        return demo_wa(fd, b, 1);
    }}
    while x > 0 {{
        store8(b, d, (48 + x % 10) as u8);
        x = x / 10;
        d = d + 1;
    }}
    let q: u32 = 0;
    while q < d {{
        store8(b, 32 + q, load8(b, d - 1 - q) as u8);
        q = q + 1;
    }}
    return demo_wa(fd, ptr_add(b, 32), d as u64);
}}

fn demo_ck(p: ptr) -> u32 {{
    let s: u32 = 0;
    let i: u32 = 0;
    while i < 256 {{
        s = s + load8(p, i);
        i = i + 1;
    }}
    return s;
}}

fn _start() -> u32 {{
    let mp: i64 = syscall7(9, 0, 262144, 3, 34, 0, 0);
    if mp < 0 {{
        return 2;
    }}
    let buf: ptr = mp as ptr;
    let data: ptr = ptr_add(buf, 512);
    let save: ptr = ptr_add(buf, 131072);
    let idx: ptr = ptr_add(buf, 160000);
    let cand: str = "{cand}";
    let i: u32 = 0;
    while i < 256 {{
        store8(idx, i, (i * 3 + 7) as u8);
        i = i + 1;
    }}
    let _a: i64 = demo_ws(1, "VERSION ");
    let _b: i64 = demo_wd(1, VERSION);
    let _c: i64 = demo_ws(1, " INDEX ");
    let _d: i64 = demo_wd(1, demo_ck(idx));
    let _e: i64 = demo_ws(1, "\\n");
    i = 0;
    while i < str_len(cand) {{
        store8(buf, i, str_byte(cand, i) as u8);
        i = i + 1;
    }}
    store8(buf, i, 0 as u8);
    let fd: i64 = syscall4(2, buf as u64, 0, 0);
    if fd < 0 {{
        let _f: i64 = demo_ws(1, "NOCAND\\n");
        return 0;
    }}
    let n: i64 = syscall4(0, fd as u64, data as u64, 200000);
    syscall4(3, fd as u64, 0, 0);
    let rc: u32 = hot_install(data, n as u32, save);
    let _g: i64 = demo_ws(1, "RC ");
    let _h: i64 = demo_wd(1, rc);
    let after: u32 = VERSION;
    let _i: i64 = demo_ws(1, " AFTER ");
    let _j: i64 = demo_wd(1, after);
    let _k: i64 = demo_ws(1, " INDEX ");
    let _l: i64 = demo_wd(1, demo_ck(idx));
    let _m: i64 = demo_ws(1, "\\n");
    return 0;
}}
"""


def _wsl(p: Path) -> str:
    s = str(Path(p).resolve()).replace("\\", "/")
    return "/mnt/" + s[0].lower() + s[2:]


def _tree(dst: Path, *, version: int, cand: str) -> Path:
    for pattern in _COPY:
        for src in sorted(ROOT.glob(pattern)):
            target = dst / src.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
    p = dst / "loment" / "tools" / "demotool.lomt"
    p.write_text(DEMO.format(version=version, cand=cand), encoding="utf-8", newline="\n")
    return dst


def _build(tree: Path) -> bytes:
    import lomentc
    import lomelf
    p = tree / "loment" / "tools" / "demotool.lomt"
    mod = lomentc.load(p)
    deps = lomentc.resolve_deps(mod, tree.resolve(), p.parent, entry=p)
    errs = lomentc.check(mod, deps=deps)
    if errs:
        raise SystemExit(errs[0])
    blob, _info = lomelf.compile_ll(lomentc.emit_llvm(mod, tree.resolve(), deps),
                                   hotswap=True)
    return blob


def _sign(out: Path) -> None:
    kd = Path(tempfile.mkdtemp(prefix="loment-demo-key-"))
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(kd / "key.pem"), "-out", str(kd / "cert.pem"),
                    "-subj", "/CN=loment-demo", "-days", "1"],
                   check=True, capture_output=True)
    pub = subprocess.run(["openssl", "x509", "-pubkey", "-noout", "-in", str(kd / "cert.pem")],
                         check=True, capture_output=True)
    (out / "loment-signing.pub.pem").write_bytes(pub.stdout)
    subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(kd / "key.pem"),
                    "-out", str(out / "SHA256SUMS.asc"), str(out / "SHA256SUMS")],
                   check=True, capture_output=True)


def cmd_make(args) -> int:
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    hot = work / "hot"
    os.environ["LOMENT_HOT_HOME"] = str(hot)
    tag = args.tag
    cand = f"{_wsl(hot)}/{hf.read_release(ROOT)}/incoming/{tag}/candidate.hot"

    old = _tree(work / "tree-old", version=1, cand=cand)
    new = _tree(work / "tree-new", version=2, cand=cand)
    elf = _build(old)
    (work / "demotool.elf").write_bytes(elf)
    os.chmod(work / "demotool.elf", 0o755)

    if args.tamper == "contract":
        with (new / "lom" / "fuai.lom").open("a", encoding="utf-8", newline="\n") as f:
            f.write("\n// one contract line changed\n")

    rel = work / "release"
    if rel.exists():
        shutil.rmtree(rel)
    proj = {"baseline": hf.read_release(old), "release": tag, "tree": "demo",
            "line": [{"sha": "d" * 40, "subject": "fix: bump the tool version", "kind": "fix"}],
            "evicted": []}
    out = ha.package(old, new, tag, rel, entry="loment/tools/demotool.lomt", projection=proj)
    if out is None:
        raise SystemExit("投影为空 —— 没有热更新件")
    _sign(rel)
    if args.tamper == "signature":
        asc = rel / "SHA256SUMS.asc"
        data = bytearray(asc.read_bytes())
        data[len(data) // 2] ^= 0xFF
        asc.write_bytes(bytes(data))
    print(f"[OK] 假 Pre-Release: {rel}" + (f"  (tamper: {args.tamper})" if args.tamper else ""))
    for p in sorted(rel.iterdir()):
        print(f"       {p.name}  {p.stat().st_size} B")
    # `loment hotupdate` 的落点：启动器把未知子命令转给 PATH 上的 `loment-<名字>`。
    bin_dir = work / "bin"
    bin_dir.mkdir(exist_ok=True)
    shim = bin_dir / "loment-hotupdate"
    target = (ROOT / "tools" / "loment_hotupdate.py").as_posix()
    shim.write_text("#!/usr/bin/env bash\n"
                    f'exec python "{target}" "$@"\n',
                    encoding="utf-8", newline="\n")
    os.chmod(shim, 0o755)
    print(f"     基线二进制: {work / 'demotool.elf'}")
    print(f"     命令       : PATH=\"{bin_dir.as_posix()}:$PATH\" loment hotupdate --work {work}")
    return 0


def cmd_stage(args) -> int:
    work = Path(args.work).resolve()
    os.environ["LOMENT_HOT_HOME"] = str(work / "hot")
    tree = work / "tree-old"
    try:
        got = st.fetch(args.tag, tree, src_dir=work / "release")
    except lu.Reject as e:
        print(f"REJECT {e.code}: {e.detail}", file=sys.stderr)
        return 1
    if got.parent.name == "rejected":
        rec = json.loads(got.read_text(encoding="utf-8"))
        first = (rec.get("detail") or "").splitlines()
        print(f"[REJECTED] {rec['code']}: {first[0] if first else ''}", file=sys.stderr)
        print(f"           {got}")
        return 1
    print(f"[STAGED] {got}")
    return 0


def cmd_run(args) -> int:
    work = Path(args.work).resolve()
    os.environ["LOMENT_HOT_HOME"] = str(work / "hot")
    probe = work / "demotool.elf"
    script = (f"rm -rf /tmp/loment-demo && mkdir -p /tmp/loment-demo && "
              f"cp {_wsl(probe)} /tmp/loment-demo/t && chmod +x /tmp/loment-demo/t && "
              f"cd /tmp/loment-demo && ./t")
    r = subprocess.run(["wsl", "-e", "bash", "-lc", script], capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    if r.returncode != 0:
        print(f"[exit {r.returncode}] {r.stderr[-300:]}", file=sys.stderr)
    return r.returncode


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="loment_demo_release")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("make", cmd_make), ("stage", cmd_stage), ("run", cmd_run)):
        p = sub.add_parser(name)
        p.add_argument("--work", required=True, help="临时目录（不在仓库里）")
        p.add_argument("--tag", default="v0.1.5-hot.1")
        p.add_argument("--tamper", choices=("signature", "contract"), default=None,
                       help="故意做坏一份，用来看准入会不会拒")
        p.set_defaults(fn=fn)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
