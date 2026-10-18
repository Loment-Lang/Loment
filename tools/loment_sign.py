#!/usr/bin/env python3
# loment_sign.py — 发行包签名 (docs/163)
#
# 两种签名, 管两件事:
#   1) **Authenticode** 签 Windows 可执行文件 (setup.exe; .cmd 不是 PE 所以不签) —— 让
#      Windows 能显示"发布者"、让 SmartScreen 有依据; 用 signtool (有 SDK 时) 或回退到
#      PowerShell 自带的 Set-AuthenticodeSignature。
#   2) **分离签名** SHA256SUMS (openssl) —— Linux/macOS 侧也能验证整包 (Authenticode 在
#      那边不好验), 第三方只需要公钥证书。
#
# 诚实边界 (docs/163 §1): **自签名 ≠ SmartScreen 不报警**。自签名让"签名可被密码学验证",
# 用户会看到"已签名/发布者未知"; 要消掉警告必须买 CA 的代码签名证书 (OV/EV)。
#
# 凭据纪律: 私钥/口令**只从环境变量或命令行参数读**, 源码里没有任何凭据字面量, 也不写进仓库。
#   自签名那条路走 Windows 证书存储 (CurrentUser\My) + 指纹, 全程不出现口令。
#
#   python tools/loment_sign.py --keygen                 # 生成**本机自签名**证书 (指纹 + 公钥证书)
#   python tools/loment_sign.py --sign FILE...           # 签 PE
#   python tools/loment_sign.py --verify FILE...         # 验 PE (打印状态与发布者)
#   python tools/loment_sign.py --sign-sums --verify-sums # 签/验 SHA256SUMS (openssl)
#   python tools/loment_sign.py --dist                   # 对 loment/dist 里该签的全做一遍
#   python tools/loment_sign.py --print-cmd FILE         # 只打印等价命令 (真证书/CI 用)
#   python tools/loment_sign.py --remove [--purge]       # 从证书存储删掉自签证书
#
# 退出码: 0 = 成功 / 1 = 失败 / 2 = 用法错误。

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "loment" / "dist"
SIGN = ROOT / "loment" / "build" / "sign"          # gitignored: 私钥只落在这里
THUMB = SIGN / "thumbprint.txt"
CER = SIGN / "loment-selfsigned.cer"
KEY = SIGN / "loment-signing.key.pem"              # openssl 私钥 (本机)
PUB = SIGN / "loment-signing.pem"                  # 公钥证书 (会拷进 dist 供第三方验证)
PUBKEY = SIGN / "loment-signing.pub.pem"           # 从证书里抽出的公钥 (openssl -verify 只吃这个)
GPG_FPR = SIGN / "gpg-fingerprint.txt"             # 本机签名密钥的指纹 (公开信息, 可进文档)
GPG_UID = "Loment Release Signing (dev)"
ASC_NAME = "loment-signing.asc"                    # 导出的**公钥** (随发布)
SUMS_ASC = "SHA256SUMS.asc"                        # SHA256SUMS 的 GPG 分离签名
PUBKEY_NAME = "loment-signing.pub.pem"            # 随发布的自定义公钥 (openssl 那条路)
SUBJECT = "CN=Loment Self-Signed (dev)"
SUMS_NAME = "SHA256SUMS"

# 环境变量名 (凭据只从这里/参数来)
ENV_PFX = "LOMENT_SIGN_PFX"
ENV_PASS = "LOMENT_SIGN_PFX_PASS"


def _shown(p: Path) -> str:
    """给日志用的路径 —— SIGN 被指到仓库外 (测试) 时不能 relative_to。"""
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return str(p)


def _sh(*argv: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), capture_output=True, text=True, shell=False,
                          encoding="utf-8", errors="replace", cwd=str(ROOT), timeout=timeout)


def _ps(script: str, timeout: int = 300,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """跑一段 PowerShell (Windows 自带; 没有它就没法做 Authenticode)。

    `env` 是**整份子进程环境**: 口令、证书路径、目标路径都从这里交给脚本, 由脚本用
    `$env:` 读 —— 它们绝不进 argv, 也就不出现在进程命令行/日志里 (#426)。
    """
    ps = "powershell" if os.name == "nt" else "pwsh"
    return subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                          capture_output=True, text=True, shell=False, env=env,
                          encoding="utf-8", errors="replace", cwd=str(ROOT), timeout=timeout)


def _env_with(**values: str) -> dict[str, str]:
    """复制当前环境并放上这次的动态值 (不修改 os.environ)。"""
    env = dict(os.environ)
    env.update(values)
    return env


def find_signtool() -> str | None:
    """PATH 上没有就找 Windows SDK 里的 (signtool 不装 SDK 就没有)。"""
    import glob
    p = _sh("where", "signtool") if os.name == "nt" else None
    if p and p.returncode == 0 and p.stdout.strip():
        return p.stdout.strip().splitlines()[0]
    for pat in (r"C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe",
                r"C:\Program Files (x86)\Windows Kits\10\bin\*\x86\signtool.exe"):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None


def cert_spec(a) -> tuple[str | None, str | None, str | None]:
    """返回 (pfx, 口令, 指纹)。优先级: 参数 > 环境变量 > 本机自签。"""
    pfx = a.cert or os.environ.get(ENV_PFX) or None
    pw = a.passwd or os.environ.get(ENV_PASS) or None
    thumb = THUMB.read_text(encoding="utf-8").strip() if THUMB.exists() else None
    return pfx, pw, thumb


# ---------------------------------------------------------------- keygen / remove

def keygen(subject: str = SUBJECT) -> int:
    SIGN.mkdir(parents=True, exist_ok=True)
    # 先清掉同主题的旧证书: 反复 --keygen 不该在存储里堆一串同名证书
    # (否则"这份文件是谁签的"要看指纹才知道, 而且旧证书仍能签名)
    _ps("Get-ChildItem Cert:\\CurrentUser\\My | "
        "Where-Object { $_.Subject -eq $env:LOMENT_SIGN_SUBJECT } | "
        "Remove-Item -Force -ErrorAction SilentlyContinue",
        env=_env_with(**{"LOMENT_SIGN_SUBJECT": subject}))
    r = _ps(
        "$c = New-SelfSignedCertificate -Type CodeSigningCert -Subject $env:LOMENT_SIGN_SUBJECT "
        "-KeyUsage DigitalSignature -CertStoreLocation Cert:\\CurrentUser\\My "
        "-NotAfter (Get-Date).AddYears(2) -FriendlyName 'Loment dev signing'; "
        "Export-Certificate -Cert $c -FilePath $env:LOMENT_SIGN_CER | Out-Null; "
        "Write-Output $c.Thumbprint",
        env=_env_with(**{"LOMENT_SIGN_SUBJECT": subject, "LOMENT_SIGN_CER": str(CER)}))
    if r.returncode != 0 or not r.stdout.strip():
        print(f"[ERR] 生成自签证书失败: {(r.stderr or r.stdout)[-300:]}", file=sys.stderr)
        return 1
    thumb = r.stdout.strip().splitlines()[-1].strip()
    THUMB.write_text(thumb + "\n", encoding="utf-8", newline="\n")
    print(f"[OK] 自签代码签名证书 (CurrentUser\\My): {subject}")
    print(f"     指纹 {thumb}")
    print(f"     公钥证书 {_shown(CER)}")
    # 分离签名用一对 openssl 密钥 (跨平台可验); 私钥只在本机
    r = _sh("openssl", "req", "-x509", "-newkey", "rsa:3072", "-nodes",
            "-keyout", str(KEY), "-out", str(PUB), "-days", "730", "-subj", f"/{subject}")
    if r.returncode != 0:
        print(f"[WARN] openssl 密钥生成失败 (分离签名不可用): {(r.stderr or '')[-200:]}")
    else:
        _export_pubkey()
        print(f"[OK] openssl 密钥对: {_shown(PUBKEY)} (验签用) / 私钥只在本机")
    print("     注意: 自签名**不会**让 SmartScreen 不报警 —— 换 CA 证书见 docs/163")
    return 0


def trust(add: bool) -> int:
    """把自签证书放进**本机**受信存储 (CurrentUser), 让本机的签名状态变成 Valid。

    这是本机的信任模型改动 (任何用这把钥匙签的东西在本机都会被信任), 所以默认不做,
    要显式 `--trust`。只影响当前用户, `--untrust` 可撤。
    """
    if not THUMB.exists():
        print("[ERR] 没有本机自签证书 (先跑 --keygen)", file=sys.stderr)
        return 1
    thumb = THUMB.read_text(encoding="utf-8").strip()
    verb = "Add" if add else "Remove"
    stores = ["Cert:\\CurrentUser\\TrustedPublisher", "Cert:\\CurrentUser\\Root"]
    script = "$t = $env:LOMENT_SIGN_THUMB; " + "; ".join(
        (f"{verb}-Item -Path (\"Cert:\\CurrentUser\\My\\\" + $t) "
         f"-CertStoreLocation '{s}'" if add else
         f"Remove-Item -Path ('{s}\\' + $t) -Force -ErrorAction SilentlyContinue")
        for s in stores)
    r = _ps(script, env=_env_with(**{"LOMENT_SIGN_THUMB": thumb}))
    if r.returncode != 0:
        print(f"[ERR] 信任操作失败: {(r.stderr or '')[-200:]}", file=sys.stderr)
        return 1
    print(f"[OK] 已{'信任' if add else '取消信任'}本机自签证书 {thumb} "
          f"(CurrentUser\\TrustedPublisher + Root)")
    return 0


def remove(purge: bool) -> int:
    if THUMB.exists():
        thumb = THUMB.read_text(encoding="utf-8").strip()
        r = _ps("Remove-Item -Path (\"Cert:\\CurrentUser\\My\\\" + $env:LOMENT_SIGN_THUMB) "
                "-Force -ErrorAction SilentlyContinue; Write-Output done",
                env=_env_with(**{"LOMENT_SIGN_THUMB": thumb}))
        print(f"[OK] 已从证书存储删除 {thumb}" if r.returncode == 0 else f"[WARN] 删除失败: {r.stderr[-160:]}")
        THUMB.unlink()
    if GPG_FPR.exists():
        fpr = GPG_FPR.read_text(encoding="utf-8").strip()
        r = _sh("gpg", "--batch", "--yes", "--delete-secret-and-public-key", fpr)
        print(f"[OK] 已删除 GPG 密钥 {fpr[-16:]}" if r.returncode == 0
              else f"[WARN] GPG 密钥删除失败: {(r.stderr or '')[-160:]}")
        GPG_FPR.unlink()
    if purge and SIGN.exists():
        import shutil
        shutil.rmtree(SIGN)
        print(f"[OK] 已删除 {_shown(SIGN)} (含私钥)")
    return 0


# ---------------------------------------------------------------- Authenticode

def _authenticode_script(use_pfx: bool, ts: bool) -> str:
    """Set-AuthenticodeSignature 脚本 —— 动态值一律经 `$env:` 读，不做字符串拼接。

    这是 #426 的根：口令/路径一旦被 `'...'` 拼进脚本，它们就同时出现在
    `powershell.exe` 的命令行上（任何能读进程命令行的人都能看到），而且带撇号的路径
    会提前结束引号、把后半段变成 PowerShell 代码。脚本本身现在是**常量**。
    """
    sign = ("$s = Set-AuthenticodeSignature -Certificate $c "
            "-FilePath $env:LOMENT_SIGN_FILE -HashAlgorithm SHA256")
    if ts:
        sign += " -TimestampServer $env:LOMENT_SIGN_TS"
    if use_pfx:
        head = (f"$c = New-Object System.Security.Cryptography.X509Certificates."
                f"X509Certificate2($env:{ENV_PFX}, $env:{ENV_PASS}); ")
    else:
        head = '$c = Get-Item ("Cert:\\CurrentUser\\My\\" + $env:LOMENT_SIGN_THUMB); '
    return head + sign + "; Write-Output ($s.Status.ToString() + '|' + $s.StatusMessage)"


def sign_pe(files: list[str], a) -> int:
    pfx, pw, thumb = cert_spec(a)
    if not pfx and not thumb:
        print("[ERR] 没有可用证书: 要么设 LOMENT_SIGN_PFX(+/LOMENT_SIGN_PFX_PASS), "
              "要么先跑 --keygen (docs/163)", file=sys.stderr)
        return 1
    st = find_signtool()
    rc = 0
    for f in files:
        p = Path(f)
        if not p.exists():
            print(f"[ERR] 文件不存在: {f}", file=sys.stderr)
            rc = 1
            continue
        if pfx:
            if not pw:
                print(f"[ERR] 给了证书 {ENV_PFX}/--cert 但没给口令 "
                      f"({ENV_PASS}/--pass)", file=sys.stderr)
                return 1
            if st:
                argv = [st, "sign", "/f", pfx, "/p", pw, "/fd", "sha256"]
                if a.ts:
                    argv += ["/tr", a.ts, "/td", "sha256"]
                argv += [str(p)]
                r = _sh(*argv)
                status = "signtool rc=0" if r.returncode == 0 else ""
            else:
                # #426: password, PFX path and target path go through the CHILD'S
                # ENVIRONMENT, never argv and never spliced into the -Command string.
                # The passphrase would otherwise be readable from process command lines
                # (another user, an endpoint agent, a log), and `'{p}'` ends the quoted
                # string when a path contains an apostrophe.
                env = _env_with(**{ENV_PFX: pfx, ENV_PASS: pw,
                                   "LOMENT_SIGN_FILE": str(p)})
                if a.ts:
                    env["LOMENT_SIGN_TS"] = a.ts
                r = _ps(_authenticode_script(True, bool(a.ts)), env=env)
                status = (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else ""
        else:
            r = _ps(_authenticode_script(False, False),
                    env=_env_with(**{"LOMENT_SIGN_FILE": str(p),
                                     "LOMENT_SIGN_THUMB": thumb or ""}))
            status = (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else ""
        # **必须看返回的状态**: Set-AuthenticodeSignature 签名失败时既不抛异常也不改退出码,
        # 只看 rc 会报"已签名"而文件其实没签 (2026-09-12 真踩到: verify 立刻说 NotSigned)
        st_name = status.split("|")[0].strip()
        ok = r.returncode == 0 and (st_name in ("UnknownError", "Valid", "signtool rc=0"))
        if not ok:
            # 刚 --keygen 出来的证书偶尔第一次签不上 (私钥容器还没就绪): 隔 1 秒重试一次
            import time
            time.sleep(1.0)
            r2 = _ps(_authenticode_script(False, False),
                     env=_env_with(**{"LOMENT_SIGN_FILE": str(p),
                                      "LOMENT_SIGN_THUMB": thumb or ""}))
            status2 = (r2.stdout or "").strip().splitlines()[-1] if r2.stdout.strip() else ""
            st2 = status2.split("|")[0].strip()
            if status2 and st2 in ("UnknownError", "Valid"):
                ok, status, st_name = True, status2, st2
        if not ok:
            print(f"[ERR] 签 {p.name} 失败: {status or (r.stderr or r.stdout)[-200:]}",
                  file=sys.stderr)
            rc = 1
            continue
        print(f"[OK] 已签名 {p.name} ({p.stat().st_size} 字节)  via "
              f"{'signtool' if (pfx and st) else 'Set-AuthenticodeSignature'}"
              f"{'' if st_name == 'signtool rc=0' else ' / 状态 ' + st_name}")
    return rc


def verify_pe(files: list[str], strict: bool) -> int:
    rc = 0
    for f in files:
        p = Path(f)
        if not p.exists():
            print(f"[ERR] 文件不存在: {f}", file=sys.stderr)
            rc = 1
            continue
        r = _ps("$s = Get-AuthenticodeSignature -FilePath $env:LOMENT_SIGN_FILE; "
                "$sub = if ($s.SignerCertificate) { $s.SignerCertificate.Subject } else { '-' }; "
                "Write-Output ($s.Status.ToString() + '|' + $sub)",
                env=_env_with(**{"LOMENT_SIGN_FILE": str(p)}))
        line = (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else "|"
        status, _, sub = line.partition("|")
        signed = sub not in ("", "-")
        # 自签名链不受信: Status = UnknownError 但**签名本身存在** —— 这是正常的, 分开报
        ok = signed and (not strict or status == "Valid")
        print(f"{'[OK]' if ok else '[ERR]'} {p.name}: 签名={'有' if signed else '无'} "
              f"状态={status} 发布者={sub}")
        if not ok:
            rc = 1
    return rc


# ---------------------------------------------------------------- 分离签名 (SHA256SUMS)

def _export_pubkey() -> None:
    """从证书里抽出**公钥** —— `openssl dgst -verify` 只吃公钥 PEM, 给证书会报
    "Could not find private key of public key" (OpenSSL 3 的行为)。"""
    r = _sh("openssl", "x509", "-in", str(PUB), "-pubkey", "-noout")
    if r.returncode == 0 and r.stdout.strip():
        PUBKEY.write_text(r.stdout, encoding="utf-8", newline="\n")


# ---------------------------------------------------------------- GPG (分离签名 + 哈希校验)

def keygen_gpg(uid: str = GPG_UID) -> int:
    """生成一把**本机**签名密钥 (GnuPG 钥匙串, 私钥永不导出)。

    密钥在 ~/.gnupg, 仓库里只出现①公钥②指纹 —— 指纹是公开信息, 写进文档才好让第三方
    确认"验签用的确实是这把钥匙"。开发密钥不给口令 (无人值守 CI 要能跑), 换真密钥时
    该怎么给口令由用的人决定。
    """
    SIGN.mkdir(parents=True, exist_ok=True)
    r = _sh("gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", "",
            "--quick-generate-key", uid, "ed25519", "sign", "2y")
    if r.returncode != 0:
        print(f"[ERR] GPG 生成密钥失败: {(r.stderr or r.stdout)[-300:]}", file=sys.stderr)
        return 1
    fpr = _gpg_fingerprint(uid)
    if not fpr:
        print("[ERR] 生成了密钥但读不到指纹", file=sys.stderr)
        return 1
    GPG_FPR.write_text(fpr + "\n", encoding="utf-8", newline="\n")
    pub = OUT / ASC_NAME
    OUT.mkdir(parents=True, exist_ok=True)
    r = _sh("gpg", "--batch", "--yes", "--armor", "--export", "--output", str(pub), fpr)
    if r.returncode != 0:
        print(f"[WARN] 公钥导出失败: {(r.stderr or '')[-200:]}")
    print(f"[OK] GPG 密钥: {uid}")
    print(f"     指纹 {fpr}")
    print(f"     公钥 {_shown(pub)} (随发布; 指纹请另找渠道核对)")
    return 0


def _gpg_fingerprint(uid: str) -> str:
    r = _sh("gpg", "--batch", "--with-colons", "--list-keys", uid)
    for line in (r.stdout or "").splitlines():
        if line.startswith("fpr:"):
            return line.split(":")[9]
    return ""


def sign_gpg(sums: Path, uid: str = GPG_UID) -> int:
    if not sums.exists():
        print(f"[ERR] 缺 {sums}", file=sys.stderr)
        return 1
    fpr = GPG_FPR.read_text(encoding="utf-8").strip() if GPG_FPR.exists() else _gpg_fingerprint(uid)
    if not fpr:
        print(f"[ERR] 没有 GPG 密钥 (先跑 --keygen-gpg)", file=sys.stderr)
        return 1
    asc = sums.with_name(SUMS_ASC)
    r = _sh("gpg", "--batch", "--yes", "--armor", "--detach-sign",
            "--local-user", fpr, "--output", str(asc), str(sums))
    if r.returncode != 0:
        print(f"[ERR] GPG 分离签名失败: {(r.stderr or '')[-240:]}", file=sys.stderr)
        return 1
    print(f"[OK] {SUMS_ASC} ({asc.stat().st_size} 字节) —— 密钥 {fpr[-16:]}")
    return 0


def verify_gpg(sums: Path, want_fpr: str | None = None) -> int:
    """验 GPG 分离签名。**不仅要 gpg 说 Good signature, 还要核对指纹** ——
    任何密钥签出来的都叫 Good signature, 不核对指纹等于没验身份。"""
    asc = sums.with_name(SUMS_ASC)
    if not asc.exists():
        print(f"[ERR] 缺 {asc.name} (先跑 --sign-gpg)", file=sys.stderr)
        return 1
    r = _sh("gpg", "--batch", "--status-fd", "1", "--verify", str(asc), str(sums))
    out = (r.stdout or "") + (r.stderr or "")
    good = "GOODSIG" in out and r.returncode == 0
    used = ""
    for line in out.splitlines():
        if line.startswith("[GNUPG:] VALIDSIG"):
            used = line.split()[2]
    want = want_fpr or (GPG_FPR.read_text(encoding="utf-8").strip() if GPG_FPR.exists() else "")
    match = (not want) or (used == want)
    ok = good and match
    print(f"{'[OK]' if ok else '[ERR]'} {asc.name}: 签名={'Good' if good else 'BAD'} "
          f"指纹={used or '-'}{'' if match else ' (与记录不符: ' + want + ')'}")
    return 0 if ok else 1


def sign_sums(sums: Path) -> int:
    if not KEY.exists():
        print(f"[ERR] 缺私钥 {_shown(KEY)} (先跑 --keygen)", file=sys.stderr)
        return 1
    if not sums.exists():
        print(f"[ERR] 缺 {sums}", file=sys.stderr)
        return 1
    sig = sums.with_name(sums.name + ".sig")
    r = _sh("openssl", "dgst", "-sha256", "-sign", str(KEY), "-out", str(sig), str(sums))
    if r.returncode != 0:
        print(f"[ERR] 分离签名失败: {(r.stderr or '')[-240:]}", file=sys.stderr)
        return 1
    import shutil
    if not PUBKEY.exists():
        _export_pubkey()
    shutil.copyfile(PUB, OUT / PUB.name)          # 证书: 让第三方看身份
    shutil.copyfile(PUBKEY, OUT / PUBKEY.name)    # 公钥: 让第三方验签
    print(f"[OK] {sums.name}.sig ({sig.stat().st_size} 字节); 证书与公钥已拷到 "
          f"{_shown(OUT / PUBKEY.name)}")
    return 0


def verify_sums(sums: Path, cert: Path | None = None) -> int:
    sig = sums.with_name(sums.name + ".sig")
    pub = cert or ((OUT / PUBKEY.name) if (OUT / PUBKEY.name).exists() else PUBKEY)
    if not sig.exists() or not pub.exists():
        print(f"[ERR] 缺 {sig.name} 或公钥 {pub}", file=sys.stderr)
        return 1
    r = _sh("openssl", "dgst", "-sha256", "-verify", str(pub), "-signature", str(sig), str(sums))
    ok = r.returncode == 0 and "Verified OK" in (r.stdout or "")
    print(f"{'[OK]' if ok else '[ERR]'} {sums.name} 分离签名: {(r.stdout or r.stderr).strip()[:80]}")
    return 0 if ok else 1


# ---------------------------------------------------------------- 打印命令 (真证书/CI)

def print_cmd(files: list[str], a) -> int:
    st = find_signtool() or "signtool.exe"
    ts = ["/tr", a.ts or "<RFC3161 时间戳 URL>", "/td", "sha256"]
    print("# 真 CA 证书 (OV/EV) 的签名命令 —— 证书路径与口令从环境/密钥服务取, 别写进仓库")
    print(f'  "{st}" sign /f "%{ENV_PFX}%" /p "%{ENV_PASS}%" /fd sha256 '
          + " ".join(ts) + " " + " ".join(files))
    print("# 分离签名 (跨平台验证)")
    print(f"  openssl dgst -sha256 -sign <私钥> -out {SUMS_NAME}.sig {SUMS_NAME}")
    print(f"  openssl dgst -sha256 -verify <公钥证书> -signature {SUMS_NAME}.sig {SUMS_NAME}")
    print("# GPG 分离签名 (第三方只需公钥 + 核对指纹)")
    print(f"  gpg --armor --detach-sign --local-user <FPR> -o {SUMS_ASC} {SUMS_NAME}")
    print(f"  gpg --verify {SUMS_ASC} {SUMS_NAME} && sha256sum -c {SUMS_NAME}")
    return 0


def write_verify_scripts() -> None:
    """往 loment/dist 里放两个**给下载者用**的校验脚本 (哈希 + 有签名就一起验)。"""
    OUT.mkdir(parents=True, exist_ok=True)
    sh = f"""#!/bin/sh
# Loment 发行包校验 (由 tools/loment_sign.py 生成)
# 用法: sh verify.sh            # 在解包/下载目录里跑
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
echo "== 1/3 哈希 =="
sha256sum -c {SUMS_NAME}
if [ -f {ASC_NAME} ]; then
    echo "== 2/3 GPG 分离签名 =="
    gpg --verify {SUMS_ASC} {SUMS_NAME}
    echo "   指纹应是: $(cat FINGERPRINT 2>/dev/null || echo '(见 docs/163)')"
elif [ -f {SUMS_NAME}.sig ]; then
    echo "== 2/3 openssl 分离签名 =="
    openssl dgst -sha256 -verify {PUBKEY_NAME} -signature {SUMS_NAME}.sig {SUMS_NAME}
else
    echo "== 2/3 跳过 (没有分离签名) =="
fi
echo "== 3/3 Windows 可执行文件的 Authenticode =="
echo "   在 Windows 上跑 verify.ps1 看发布者 (自签名会显示'未知发布者')"
"""
    ps1 = f"""# Loment 发行包校验 (由 tools/loment_sign.py 生成; ASCII only)
# 用法: powershell -ExecutionPolicy Bypass -File verify.ps1
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $MyInvocation.MyCommand.Path)
Write-Host '== 1/3 SHA256 =='
$bad = 0
foreach ($line in Get-Content -LiteralPath '{SUMS_NAME}') {{
    if ($line -notmatch '^([0-9a-f]{{64}})\\s+(.+)$') {{ continue }}
    $want = $Matches[1]; $rel = $Matches[2]
    if (-not (Test-Path -LiteralPath $rel)) {{ Write-Host "[MISS] $rel"; $bad++; continue }}
    $got = (Get-FileHash -LiteralPath $rel -Algorithm SHA256).Hash.ToLower()
    if ($got -ne $want) {{ Write-Host "[BAD ] $rel"; $bad++ }} else {{ Write-Host "[OK  ] $rel" }}
}}
if ($bad -gt 0) {{ throw "$bad file(s) failed the hash check" }}
Write-Host '== 2/3 GPG signature (if present) =='
if (Test-Path -LiteralPath '{SUMS_ASC}') {{
    gpg --verify '{SUMS_ASC}' '{SUMS_NAME}'
    if ($LASTEXITCODE -ne 0) {{ throw 'gpg --verify failed' }}
    if (Test-Path -LiteralPath 'FINGERPRINT') {{ Write-Host ('expected fingerprint: ' + (Get-Content FINGERPRINT)) }}
}} else {{ Write-Host '(no detached signature in this drop)' }}
Write-Host '== 3/3 Authenticode =='
Get-ChildItem -Filter '*-setup.exe' | ForEach-Object {{
    $s = Get-AuthenticodeSignature -FilePath $_.FullName
    Write-Host ("{{0}}: {{1}} / {{2}}" -f $_.Name, $s.Status, $s.SignerCertificate.Subject)
}}
Write-Host 'verify: done'
"""
    (OUT / "verify.sh").write_text(sh, encoding="utf-8", newline="\n")
    (OUT / "verify.ps1").write_text(ps1.replace("\n", "\r\n"), encoding="utf-8", newline="")
    print(f"[OK] {_shown(OUT / 'verify.sh')} / verify.ps1 (给下载者的校验脚本)")


# ---------------------------------------------------------------- dist 一键

def do_dist(a) -> int:
    setups = sorted(OUT.glob("*-setup.exe"))
    if not setups:
        print(f"[ERR] {_shown(OUT)} 里没有 *-setup.exe (先跑 loment_dist --emit)",
              file=sys.stderr)
        return 1
    rc = 0
    if a.sign:
        rc |= sign_pe([str(p) for p in setups], a)
        # 签名改了 PE 的字节 -> 校验和清单必须在签名**之后**重算, 否则清单对不上
        import loment_dist
        sums = loment_dist.write_sums(OUT)
        print(f"[OK] 签名后重算 {_shown(sums)}")
    if a.sign_sums:
        rc |= sign_sums(OUT / SUMS_NAME)
    if a.verify or a.sign:
        rc |= verify_pe([str(p) for p in setups], a.strict)
    if a.verify_sums or a.sign_sums:
        rc |= verify_sums(OUT / SUMS_NAME)
    if a.sign_gpg:
        rc |= sign_gpg(OUT / SUMS_NAME, a.gpg_user or GPG_UID)
    if a.verify_gpg or a.sign_gpg:
        rc |= verify_gpg(OUT / SUMS_NAME, a.gpg_user)
    write_verify_scripts()
    (OUT / "FINGERPRINT").write_text(
        (GPG_FPR.read_text(encoding="utf-8") if GPG_FPR.exists() else "(no gpg key)\n"),
        encoding="utf-8", newline="\n")
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loment_sign")
    ap.add_argument("--keygen", action="store_true", help="生成本机自签名代码签名证书")
    ap.add_argument("--subject", metavar="DN", help=f"自签证书主题 (默认 {SUBJECT})")
    ap.add_argument("--keygen-gpg", action="store_true", help="生成 GPG 签名密钥 (本机钥匙串)")
    ap.add_argument("--sign-gpg", action="store_true", help="对 SHA256SUMS 做 GPG 分离签名")
    ap.add_argument("--verify-gpg", action="store_true", help="验 GPG 分离签名 (并核对指纹)")
    ap.add_argument("--gpg-user", metavar="UID", help=f"GPG 身份 (默认 {GPG_UID})")
    ap.add_argument("--trust", action="store_true", help="把自签证书放进本机受信存储 (改本机信任模型)")
    ap.add_argument("--untrust", action="store_true", help="撤销上面的信任")
    ap.add_argument("--remove", action="store_true", help="从证书存储删除自签证书")
    ap.add_argument("--purge", action="store_true", help="连本机私钥目录一起删")
    ap.add_argument("--sign", action="store_true", help="签 PE")
    ap.add_argument("--verify", action="store_true", help="验 PE")
    ap.add_argument("--sign-sums", action="store_true", help="分离签名 SHA256SUMS")
    ap.add_argument("--verify-sums", action="store_true", help="验 SHA256SUMS 的分离签名")
    ap.add_argument("--dist", action="store_true", help="对 loment/dist 全做一遍")
    ap.add_argument("--print-cmd", action="store_true", help="只打印等价命令")
    ap.add_argument("--strict", action="store_true", help="验证时要求状态 Valid (自签名不满足)")
    ap.add_argument("--cert", metavar="PFX", help=f"证书文件 (默认读环境变量 {ENV_PFX})")
    ap.add_argument("--pass", dest="passwd", metavar="PW", help=f"证书口令 (默认读环境变量 {ENV_PASS})")
    ap.add_argument("--ts", metavar="URL", help="RFC3161 时间戳服务 (自签名/离线时不要给)")
    ap.add_argument("files", nargs="*", help="要处理的 PE 文件")
    a = ap.parse_args(argv)

    if a.keygen:
        return keygen(a.subject or SUBJECT)
    if a.keygen_gpg:
        return keygen_gpg(a.gpg_user or GPG_UID)
    if a.trust or a.untrust:
        return trust(a.trust)
    if a.remove:
        return remove(a.purge)
    if a.print_cmd:
        return print_cmd(a.files or ["<file>.exe"], a)
    # --dist 必须排在所有单动作之前: 它是"把给的动作都做一遍", 被单动作抢先返回会把
    # 其余动作静默丢掉 (2026-09-12 就是这么丢了一次 --sign-sums: .sig 是旧的, 清单是新的,
    # 隔了一个小时才由 verify 报出来)
    if a.dist:
        return do_dist(a)
    if a.sign_gpg:
        return sign_gpg(OUT / SUMS_NAME, a.gpg_user or GPG_UID)
    if a.verify_gpg:
        return verify_gpg(OUT / SUMS_NAME, a.gpg_user)
    if a.sign:
        return sign_pe(a.files, a)
    if a.verify:
        return verify_pe(a.files, a.strict)
    if a.sign_sums:
        return sign_sums(OUT / SUMS_NAME)
    if a.verify_sums:
        return verify_sums(OUT / SUMS_NAME)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
