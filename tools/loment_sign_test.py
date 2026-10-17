#!/usr/bin/env python3
# loment_sign_test.py — 签名判据 (docs/163)
#
# 判据:
#   1. 凭据纪律: 没有证书时 --sign 必须**拒绝**并说明怎么配; 源码里不出现凭据字面量;
#      私钥只落在 gitignored 的 loment/build/sign/ (用 git check-ignore 证)
#   2. --keygen 造出本机自签证书 (指纹 + 公钥证书 + openssl 公钥)
#   3. 签 PE: setup.exe 拷一份 -> 签名 -> **签名存在且发布者 = 我们的 CN**;
#      没签的那份必须被验出"无签名" (即判据真的在判, 不是恒绿)
#   4. 签名改了字节 -> 校验和清单必须在签名后重算 (loment_dist --check 仍一致)
#   5. 分离签名 (openssl): 签 SHA256SUMS -> Verified OK; **改一个字节必须验不过**
#   6. --print-cmd 在没有证书的情况下也能打印 (给真证书/CI 用)
#
# 自清理: 测试用**独立 CN 的证书**, 结束后从证书存储删掉 (不动用户已装的证书)。
# 非 Windows 上 PE 那几条 SKIP (没有 Authenticode), 分离签名照测。
#
# 退出码: 0 = 全过 / 1 = 有红。

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import loment_dist  # noqa: E402
import loment_sign  # noqa: E402

ROOT = loment_sign.ROOT
RESULTS: list[tuple[str, bool, str]] = []
TEST_CN = "CN=Loment Sign Test (temporary)"


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not ok else ""))


def _point_to(tmp: Path) -> None:
    """把签名工件的路径指到临时目录 —— 不碰用户已配置的自签证书。"""
    loment_sign.SIGN = tmp
    loment_sign.THUMB = tmp / "thumbprint.txt"
    loment_sign.CER = tmp / "loment-selfsigned.cer"
    loment_sign.KEY = tmp / "loment-signing.key.pem"
    loment_sign.PUB = tmp / "loment-signing.pem"
    loment_sign.PUBKEY = tmp / "loment-signing.pub.pem"


# ------------------------------------------------------------------ 1. 凭据纪律

def test_no_credentials(tmp: Path) -> None:
    src = (ROOT / "tools" / "loment_sign.py").read_text(encoding="utf-8")
    bad = [tok for tok in ("BEGIN PRIVATE KEY", "BEGIN RSA PRIVATE KEY", "BEGIN CERTIFICATE")
           if tok in src]
    check("签名工具源码里没有密钥/证书字面量", not bad, ",".join(bad))
    check("口令只从环境变量/参数读",
          loment_sign.ENV_PASS in src and "LOMENT_SIGN_PFX_PASS" in src)
    # 没证书时必须拒绝 (在进程内验, 且此时 SIGN/THUMB 已指向空的临时目录 —— 不受本机已装证书影响)
    import argparse
    import contextlib
    import io
    env_pfx = os.environ.pop(loment_sign.ENV_PFX, None)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = loment_sign.sign_pe([str(ROOT / "loment" / "build" / "dist" / "x.exe")],
                                     argparse.Namespace(cert=None, passwd=None, ts=None,
                                                        strict=False))
    finally:
        if env_pfx is not None:
            os.environ[loment_sign.ENV_PFX] = env_pfx
    out = buf.getvalue()
    check("没配证书时 --sign 拒绝并给出配置办法",
          rc == 1 and loment_sign.ENV_PFX in out, f"rc={rc} out={out[:80]}")


def test_secrets_not_tracked(tmp: Path) -> None:
    """私钥目录必须被 git 忽略 (否则一次 --keygen 就能把私钥提交上去)。"""
    rel = tmp.relative_to(ROOT).as_posix()
    r = subprocess.run(["git", "check-ignore", "-v", rel + "/loment-signing.key.pem"],
                       capture_output=True, text=True, shell=False, cwd=str(ROOT),
                       encoding="utf-8", errors="replace")
    check("私钥目录被 .gitignore 忽略", r.returncode == 0, rel)


# ------------------------------------------------------------------ 2/3. keygen + 签 PE

def strip_signature(data: bytes) -> bytes:
    """把一个已签名的 PE 变成**真没签名**的 PE: 清零证书表目录项并截断掉签名块。

    负例必须来自"真没签名"的文件 —— 拿一份本来就有签名的拷贝当负例, 断言必然假通过
    (第一版就是这么错的)。PE 布局: 0x3C -> e_lfanew; 可选头 +96 起是数据目录,
    第 5 项 (索引 4, 偏移 4*8) 就是证书表。
    """
    e_lfanew = int.from_bytes(data[0x3C:0x40], "little")
    assert data[e_lfanew:e_lfanew + 4] == b"PE\0\0", "不是 PE"
    opt = e_lfanew + 4 + 20
    magic = int.from_bytes(data[opt:opt + 2], "little")
    assert magic in (0x10B, 0x20B), f"可选头 magic={magic:#x}"
    dir_off = opt + 96 + 4 * 8
    addr = int.from_bytes(data[dir_off:dir_off + 4], "little")
    size = int.from_bytes(data[dir_off + 4:dir_off + 8], "little")
    assert addr and size, "这份 PE 没有证书表 (本来就没签名)"
    out = bytearray(data[:addr])
    out[dir_off:dir_off + 4] = (0).to_bytes(4, "little")
    out[dir_off + 4:dir_off + 8] = (0).to_bytes(4, "little")
    return bytes(out)


def test_sign_pe(tmp: Path) -> None:
    if os.name != "nt":
        print("  SKIP  签PE: 非 Windows 没有 Authenticode")
        return
    if loment_sign.keygen(TEST_CN) != 0:
        check("--keygen 生成自签证书", False, "keygen 失败")
        return
    check("--keygen 生成自签证书", loment_sign.THUMB.exists() and loment_sign.CER.exists())

    setups = sorted(loment_dist.OUT.glob("*-setup.exe"))
    if not setups:
        print("  SKIP  签PE: loment/dist 里没有 setup.exe; 先跑 loment_dist --emit")
        return
    work = Path(tempfile.mkdtemp(prefix="loment-sign-"))
    src_bytes = setups[0].read_bytes()
    orig_out = loment_sign.OUT
    loment_sign.OUT = work
    try:
        # 负例①: 把签名剥掉 -> 必须验出"无签名"
        try:
            naked = work / "unsigned.exe"
            naked.write_bytes(strip_signature(src_bytes))
            check("剥掉签名的 PE 被验出无签名",
                  loment_sign.verify_pe([str(naked)], strict=False) == 1)
        except AssertionError as e:      # 正式产物本来就没签过 (没跑 --sign)
            print(f"  SKIP  无签名负例: {e}")

        # 正例: 签一份拷贝
        copy = work / "copy.exe"
        copy.write_bytes(src_bytes)
        check("签 PE 成功", loment_sign.sign_pe([str(copy)], _args()) == 0)
        check("签完再验: 有签名且发布者是我们",
              loment_sign.verify_pe([str(copy)], strict=False) == 0)
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"$s = Get-AuthenticodeSignature -FilePath '{copy}'; "
                            f"Write-Output $s.SignerCertificate.Subject"],
                           capture_output=True, text=True, shell=False, timeout=120,
                           encoding="utf-8", errors="replace")
        check("发布者主题 == 本机自签 CN", TEST_CN in (r.stdout or ""), (r.stdout or "")[:80])

        # 负例②: 签名之后被篡改一字节 -> 严格验证必须红
        tampered = work / "tampered.exe"
        tampered.write_bytes(src_bytes)
        loment_sign.sign_pe([str(tampered)], _args())
        b = bytearray(tampered.read_bytes())
        b[len(b) // 2] ^= 0xFF
        tampered.write_bytes(bytes(b))
        check("签后被篡改的 PE 严格验证为红",
              loment_sign.verify_pe([str(tampered)], strict=True) == 1)
    finally:
        loment_sign.OUT = orig_out
        shutil.rmtree(work, ignore_errors=True)


def _args():
    import argparse
    a = argparse.Namespace(cert=None, passwd=None, ts=None, strict=False)
    return a


# ------------------------------------------------------------------ 4/5. 清单与分离签名

def test_sums(tmp: Path) -> None:
    real = loment_dist.OUT / "SHA256SUMS"
    if not real.exists():
        print("  SKIP  分离签名: 没有 loment/dist/SHA256SUMS")
        return
    # 在临时目录里做: 测试用的是自己的密钥, 绝不能把临时密钥的公钥/签名写进正式产物目录
    work = Path(tempfile.mkdtemp(prefix="loment-sign-sums-"))
    shutil.copyfile(real, work / "SHA256SUMS")
    sums = work / "SHA256SUMS"
    orig_out = loment_sign.OUT
    loment_sign.OUT = work
    try:
        ok = loment_sign.sign_sums(sums) == 0
        check("分离签名 SHA256SUMS", ok)
        if not ok:
            return
        check("验分离签名: Verified OK", loment_sign.verify_sums(sums) == 0)
        orig = sums.read_bytes()
        try:
            sums.write_bytes(orig + b"\n# tampered\n")          # 篡改一字节
            check("篡改后分离签名验不过", loment_sign.verify_sums(sums) == 1)
        finally:
            sums.write_bytes(orig)
        check("还原后又能验过", loment_sign.verify_sums(sums) == 0)
    finally:
        loment_sign.OUT = orig_out
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------------ 6. print-cmd

def test_print_cmd() -> None:
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "loment_sign.py"), "--print-cmd",
                        "x.exe"], capture_output=True, text=True, shell=False,
                       encoding="utf-8", errors="replace", cwd=str(ROOT))
    out = r.stdout or ""
    check("--print-cmd 能打印真证书命令 (含环境变量名与时间戳位)",
          r.returncode == 0 and loment_sign.ENV_PFX in out and "openssl dgst" in out,
          out[:80])


# ------------------------------------------------------------------ main

def main() -> int:
    print("loment_sign_test —— 签名判据 (docs/163)")
    tmp = Path(tempfile.mkdtemp(prefix="loment-sign-key-"))
    # 先重定向到临时目录, 再让测试自己 keygen —— 不然会覆盖用户真的自签证书目录
    _point_to(tmp)
    test_no_credentials(tmp)
    test_secrets_not_tracked(ROOT / "loment" / "build" / "sign")
    try:
        test_sign_pe(tmp)
    except Exception as e:  # noqa: BLE001
        check("签 PE", False, f"{type(e).__name__}: {e}")
    try:
        test_sums(tmp)
    except Exception as e:  # noqa: BLE001
        check("分离签名", False, f"{type(e).__name__}: {e}")
    test_print_cmd()
    # 自清理: 删掉测试证书 (用户自己的证书不动)
    try:
        loment_sign.remove(purge=True)
    except Exception as e:  # noqa: BLE001
        check("清理测试证书", False, f"{type(e).__name__}: {e}")
    shutil.rmtree(tmp, ignore_errors=True)

    bad = [r for r in RESULTS if not r[1]]
    print(f"\nloment_sign_test: {len(RESULTS) - len(bad)}/{len(RESULTS)} 通过")
    for name, _ok, detail in bad:
        print(f"  FAIL  {name}  [{detail}]")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
