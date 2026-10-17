<!-- CONTRIBUTING.md is the long version of everything below; this is the checklist. -->

## What this changes, and why

<!-- One paragraph. If it is a fix: what was wrong, and how you know. -->

## AI disclosure

<!-- This repository is written by people and agents together, and asks which. There is no
     `ai-generated` label here, so say it in the body. Delete this section only if a human wrote
     every line without an agent. -->

- [ ] An AI agent wrote or co-wrote this change — tool:

## The gate

<!-- See CONTRIBUTING.md, "Run the gate before you open a pull request". -->

- What I ran:
- Result: <!-- green N / red M — and for each red: known before this change, or introduced by it -->

The same gate also runs on this pull request (`.github/workflows/gate.yml`) — but **a green check
there is not a substitute for the line above.** It means the workflow finished, not that your
change is clean. CONTRIBUTING.md says where the gate stands today and what the bar is; it is not
"green".

## House rules

- [ ] If this touches the language surface: **both** implementations changed, and the parity
      criteria pass — [`docs/158`](docs/158-loment-freeze.md) first, the surface is frozen.
- [ ] Generated files are in their **own commit**, whose message says it is only generated
      output: `loment/build/selfhost_driver.ll` and `loment/tools/surface_data.lomt` are the two
      this branch carries.
- [ ] I did **not** commit the whole-tree three (`loment/build/release-manifest.json`,
      `loment/build/SHA256SUMS`, `docs/manual/`). They are recomputed on `main` after every merge
      precisely so that two open pull requests cannot collide on them —
      [`docs/254`](docs/254-the-artifacts-are-not-a-shared-register.md).
- [ ] If I touched a file the seed or the surface table is derived from, I regenerated it — **or**
      I left it to the `manifest` workflow, which recomputes those two and pushes a `[生成物] …`
      commit onto this branch. (It cannot do that for a fork pull request whose head touched
      `tools/`, or one without "Allow edits by maintainers": there, it is on me.)
- [ ] I did not widen a criterion to make it pass.
- [ ] Anything a reader would need in order to believe this is in `docs/`, not only in this
      description.
