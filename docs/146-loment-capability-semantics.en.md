<!-- translated-from: docs/146-loment-capability-semantics.md -->
<!-- source-sha256: e2c08b96a02fb4c35c3b181eb393d5f13206d401f6f3d7c49e208c63c568ba74 -->

# 146 · Formal semantics of Loment capability domains (M44)

> Status: specification draft · Related: docs/145 P4 (M35–M43) · Implementation: `Guard` in `tools/lomentc.py`
> In one line: **guard passes ⇒ the index lies inside the domain; the number of audit entries = the number of
> passing guards.** Both hold at compile time and at run time together, and depend neither on the model nor on
> the caller's goodwill.

## 1. Definition

A capability domain is a 4-tuple

```
D = (space, lo, hi, revocable)      space ∈ identifier,  0 ≤ lo ≤ hi,  revocable ∈ {true, false}
```

In Loment it is declared with `capability <name> : <space>[<lo>..<hi>] [revocable]`. A compilation unit exports
a `CapDomain` table (Rust) / an `@__loment_caps` constant table (IR), and the kernel and Potato read the same
one.

**A capability name is unit-wide, not file-wide**: the dependencies' declarations come first and the entry's
last, and they form one table; a `guard` resolves by name in that table and uses the `lo/hi` of the file that
declared it. A dependency's `guard` therefore cannot fall back to `[0..0]` because the entry did not repeat
the name, and the entry cannot silently widen the library's bound. The same name declared in two files is a
compile error (E004, positioned at the later declaration). The reference and the self-hosted implementation
share this rule (#1175).

## 2. Semantics of the guard statement

```
guard <cap>(e);
```

1. Evaluate `e` to get `idx`;
2. If `idx ∉ [lo, hi]` → **trap** (IR: `@__loment_abort`; Rust: `panic!`), **with no other side effect**;
3. Otherwise add one to the audit table `__LOMENT_AUDIT[cap]`.

## 3. Static rules (compile time)

| Case | Result |
|---|---|
| `e` is an integer literal and `idx ∉ [lo, hi]` | **compile error** (`能力 X 域 [lo..hi]，索引 idx 越界` — "capability X has domain [lo..hi], index idx out of range") |
| `e` is an integer literal and `idx ∈ [lo, hi]` | allowed (the run-time check is still generated, and may be optimised away) |
| `e` is not a literal | allowed, run-time check |
| `<cap>` is undeclared (no such name **anywhere in the unit**) | compile error |
| the same capability name is declared in two files | compile error (E004, at the later declaration) |
| `<cap>.space` hits some `excluded "<space>: ..."` | compile error (M41) |

## 4. Soundness assertions (checkable, not a formal proof)

- **S1 (inside the domain)**: any `guard` that returns successfully has its `idx` in `[lo, hi]`. Reason:
  literals are decided at compile time, non-literals are decided at run time and trap.
- **S2 (audit fidelity)**: `__LOMENT_AUDIT[cap]` equals the number of passing `guard`s in that compilation
  unit. Reason: it is incremented only on the pass branch, and there is no other write site.
- **S3 (no side effect out of range)**: the out-of-range path executes only the trap and writes no
  user-visible state.

These three are checked by the P4 cases in `tools/lomentc_test.py` and by the M43 fuzz test (120 random
`(lo,hi,idx)` triples).

## 5. What is explicitly not covered (honest boundaries)

- **No formal verification**: the above is "implementation-level assertion + test checking", not a
  machine-checked proof.
- **No information-flow analysis**: `guard` constrains only that the **index** lands inside the domain; it
  constrains nothing about where data comes from or goes to.
- **Revocation exists only as a representation**: the `revocable` flag enters the domain description table and
  the Potato object; **the actual revocation semantics are enforced by the kernel at run time** (P7/M37
  outstanding) — on the language side, all that is guaranteed today is "the declaration exists and is
  readable".
- **The Loment expression of A1–A4 (M40) and trust-adaptive domain width (M42)** need the kernel's
  capability-domain model to line up first, and are scheduled for P7.
- **`guard` is not authorisation**: it checks the index, not "does the current principal hold this
  capability"; the principal-to-capability binding lives on the kernel side.

> **Extension (2026-11-06 / 2026-11-07):** shapes / polarity / time laws / the context class are
> extended in `docs/306`–`docs/308`; all eighteen kinds are implemented in both compilers
> (`docs/306` §12). The semantics of §1–§4 here are the "regular domain + allow" case of those
> eighteen, and still hold verbatim.
