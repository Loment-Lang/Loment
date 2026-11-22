#!/usr/bin/env python3
# trust_quorum.py — R4 K-of-N 商团 (docs/379 §3.4 / C7)
#
# 商团判决: 至少 k 个**覆盖该推导档位**的内核接受同一份 (artifact, derivation,
# conclusion)，且没有内核在「接受/拒绝」上相反。沉默按拒绝处理；回避不是接受。
#
# 它只是策略层：不实现规则。内核实现见 tools/trust_kernel.py（Python，core+l0）
# 与 tools/trust_kernel_c.c（C，core）。两者零共享代码。

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trust_kernel as K  # noqa: E402

CORE = "core"
L0 = "l0"
ACCEPT = "accept"
REJECT = "reject"
UNSUPPORTED = "unsupported"

PROFILES = frozenset({CORE, L0})


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@dataclass(frozen=True)
class Kernel:
    id: str
    lang: str
    covers: frozenset[str]
    impl_sha256: str
    rules_sha256: str
    runner: Callable[[bytes, dict, dict | None], tuple[str, str]]


def profile(derivation: dict) -> str:
    """推导的档位：任何一个节点带非空 `l0` / `l0_safe` 就是 l0 档。"""
    for n in derivation.get("nodes") or []:
        j = n.get("judgment") if isinstance(n, dict) else None
        if not isinstance(j, dict):
            continue
        if j.get("l0") is not None or j.get("l0_safe") is not None:
            return L0
    return CORE


def python_kernel() -> Kernel:
    path = Path(K.__file__)
    want = _sha(path.read_bytes())

    def run(artifact: bytes, derivation: dict, conclusion: dict | None):
        if _sha(path.read_bytes()) != want:
            return REJECT, "kernel implementation changed after it was loaded"
        ok, why = K.verify(artifact, derivation, conclusion)
        return (ACCEPT if ok else REJECT), ("" if ok else why[0])

    return Kernel(id="python-kernel", lang="python", covers=frozenset({CORE, L0}),
                  impl_sha256=want, rules_sha256=K.RULES_SHA256, runner=run)


# ---------------------------------------------------------------- C 内核装载


def _linux() -> bool:
    return sys.platform.startswith("linux")


def _sh(script: str, timeout: float = 120):
    argv = (["bash", "-lc", script] if _linux()
            else ["wsl", "-e", "bash", "-lc", script])
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                          shell=False)


def _sh_path(p: Path) -> str:
    s = str(Path(p).resolve()).replace("\\", "/")
    if _linux():
        return s
    return "/mnt/" + s[0].lower() + s[2:]


class CLoader:
    """把 tools/trust_kernel_c.c 编成可执行文件并当第二个内核用。"""

    _built: dict[str, str] = {}

    def __init__(self, source: Path | None = None):
        self.source = Path(source or (Path(__file__).resolve().parent / "trust_kernel_c.c"))
        self.source_sha = _sha(self.source.read_bytes())
        self._bin: str | None = None
        self._rules: str | None = None

    def available(self) -> bool:
        try:
            r = _sh("command -v cc >/dev/null 2>&1 && echo CC", timeout=60)
        except (OSError, subprocess.SubprocessError):
            return False
        return r.returncode == 0 and "CC" in r.stdout

    def build(self) -> None:
        b = self._built.get(self.source_sha)
        if b and self._bin is None:
            self._bin = b
            return
        if self._bin is None:
            b = f"/tmp/loment-trust-kernel-c-{self.source_sha[:16]}"
            src = shlex.quote(_sh_path(self.source))
            cmd = f"cc -O2 -std=c11 -o {b} {src}"
            r = _sh(cmd, timeout=180)
            if r.returncode != 0:
                raise RuntimeError(f"cannot build the C kernel: {r.stderr[-400:]}")
            self._bin = b
            CLoader._built[self.source_sha] = b
        if self._rules is None:
            r = _sh(f"{shlex.quote(self._bin)} rules", timeout=60)
            if r.returncode != 0:
                raise RuntimeError(f"the C kernel does not print its rule table: {r.stderr[-200:]}")
            self._rules = r.stdout.strip()

    def run(self, artifact: bytes, derivation: dict, conclusion: dict | None):
        self.build()
        if _sha(self.source.read_bytes()) != self.source_sha:
            return REJECT, "C kernel source changed after it was loaded"
        with tempfile.TemporaryDirectory(prefix="trust-quorum-") as td:
            root = Path(td)
            a = root / "artifact.bin"
            d = root / "program.deriv"
            a.write_bytes(artifact)
            d.write_text(json.dumps(derivation, ensure_ascii=False, sort_keys=True,
                                    indent=1) + "\n", encoding="utf-8", newline="\n")
            args = [shlex.quote(self._bin), "verify",
                    "--artifact", shlex.quote(_sh_path(a)),
                    "--derivation", shlex.quote(_sh_path(d))]
            if conclusion is not None:
                c = root / "program.phi"
                c.write_text(json.dumps(conclusion, ensure_ascii=False, sort_keys=True,
                                        indent=1) + "\n", encoding="utf-8", newline="\n")
                args += ["--conclusion", shlex.quote(_sh_path(c))]
            r = _sh(" ".join(args), timeout=120)
        out = (r.stdout or "").strip()
        if out.startswith("ACCEPT"):
            return ACCEPT, ""
        if out.startswith("REJECT"):
            return REJECT, out.splitlines()[0]
        if out.startswith("UNSUPPORTED"):
            return UNSUPPORTED, out.splitlines()[0]
        return REJECT, f"kernel failed (exit {r.returncode}): {(r.stderr or out)[:160]}"

    def kernel(self) -> Kernel:
        self.build()
        return Kernel(id="c-kernel", lang="c", covers=frozenset({CORE}),
                      impl_sha256=self.source_sha, rules_sha256=self._rules,
                      runner=self.run)


# ---------------------------------------------------------------- 商团判决


def quorum_verify(artifact: bytes, derivation: dict, conclusion: dict | None,
                  kernels: list[Kernel], k: int) -> tuple[bool, dict]:
    detail: dict = {"profile": None, "k": k, "n": len(kernels),
                    "votes": [], "reason": ""}
    n = len(kernels)

    def no(why: str) -> tuple[bool, dict]:
        detail["reason"] = why
        return False, detail

    if n == 0:
        return no("the quorum has no kernels")
    if not isinstance(k, int) or k < 1 or k > n:
        return no(f"k={k!r} is outside 1..{n}")
    ids = [x.id for x in kernels]
    if len(set(ids)) != n:
        return no(f"kernel ids are not unique: {ids}")
    if len({x.impl_sha256 for x in kernels}) != n:
        return no("two kernels share one implementation")
    if any(x.rules_sha256 != K.RULES_SHA256 for x in kernels):
        return no("a kernel declares another rule table")

    prof = profile(derivation)
    detail["profile"] = prof
    if prof not in PROFILES:
        return no(f"unknown profile {prof!r}")
    covering = [x for x in kernels if prof in x.covers]
    detail["covering"] = [x.id for x in covering]
    if len(covering) < k:
        return no(f"coverage: profile {prof!r} has only {len(covering)} covering "
                  f"kernel(s), need k={k}")

    want_rules = (derivation.get("kernel") or {}).get("rules_sha256")
    votes: list[tuple[str, str, str]] = []
    for kern in covering:
        if kern.rules_sha256 != want_rules:
            verdict, why = REJECT, "rule table does not match the derivation"
        else:
            try:
                verdict, why = kern.runner(artifact, derivation, conclusion)
            except Exception as e:  # noqa: BLE001
                verdict, why = REJECT, f"kernel crashed: {type(e).__name__}: {e}"
        votes.append((kern.id, verdict, why))
    detail["votes"] = [{"id": i, "verdict": v, "why": w} for i, v, w in votes]

    accepts = [i for i, v, _ in votes if v == ACCEPT]
    rejects = [i for i, v, _ in votes if v == REJECT]
    abstains = [i for i, v, _ in votes if v == UNSUPPORTED]
    if abstains:
        return no(f"kernel(s) {abstains} abstained despite declaring coverage")
    if accepts and rejects:
        return no(f"kernels disagree: {accepts} accept, {rejects} reject")
    if len(accepts) < k:
        return no(f"{len(accepts)} accepting kernel(s), need k={k}")
    detail["reason"] = f"{len(accepts)}/{n} accept ({prof} profile, k={k})"
    return True, detail


def quorum_budget(kernels: list[Kernel], k: int) -> dict:
    axes = {"language"} if len({x.lang for x in kernels}) > 1 else set()
    return {"n": len(kernels), "k": k, "diversity_axes": len(axes),
            "kernels": [{"id": x.id, "lang": x.lang, "covers": sorted(x.covers),
                         "impl_sha256": x.impl_sha256} for x in kernels]}


def main(argv: list[str] | None = None) -> int:
    print("trust_quorum: import quorum_verify() / python_kernel() / CLoader; "
          "there is no CLI (the quorum is a policy, not a checker)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
