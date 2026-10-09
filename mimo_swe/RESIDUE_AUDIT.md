# Image residue audit (installed copies, caches, bytecode, second checkouts)

Question: besides git history (closed in `SETUP_SH` step 5), does any task image hold **code from after the base
commit**: an installed newer copy of the project, a build output or cache compiled from the fixed source, a second
checkout, or bytecode? The earlier trajectory audit (`results_qwen36/RUN_SUMMARY.md`, vals.ai section) only saw what
agents happened to read, so it was a lower bound. This audit inspects every image directly. No inference is involved
in the scan, which ran 2026-10-09.

## Method

Every pass runs Harbor's `nop` agent on a copy of the task whose `environment/` is byte-identical (same build hash, so
the cached snapshot is reused) and whose verifier is a probe script. The probe sees the image exactly as an agent
would, after `mimo_setup.sh`, and prints `RESIDUE <kind> ...` lines into `verifier/test-stdout.txt`.

| Pass | Job | Tasks | What it adds |
|---|---|---|---|
| 1. Full scan | `jobs/residue-all` (+ `residue-noout`) | all 2,698 | everything below; median 14 s per image |
| 2–3. Copy matching | `jobs/residue-p2`, `residue-p3` | 321 / 342 candidates | dominant-prefix matching for monorepos, history check |
| 4. File pairs | `jobs/residue-p4` | 92 | exact (repo path, copy path) of every post-base file |
| Transplant test | `jobs/residue-transplant` + nop control `residue-nopctl` | 91 / 21 | does the copy hold a working fix? |
| Fixed images | `jobs/residue-fixed-probe2` | 83 | residue gone, setup still succeeds, copies not emptied |

`residue_probe.sh` checks, per image:
1. **Project identity:** names from `package.json`, `pyproject.toml`, `setup.py`/`setup.cfg`, `go.mod`, `Cargo.toml`,
   `pom.xml`, `composer.json` and `*.gemspec` at the base commit, including monorepo sub-packages.
2. **Installed copies** anywhere on the filesystem:
   - `*.dist-info`/`*.egg-info` with the project's name, followed to their top-level modules;
   - `node_modules/<name>`, ignoring workspace symlinks back into the repo;
   - `pkg/mod/<module>@v`, cargo registry sources, `~/.m2`, `vendor/`, gems.
3. **Copy vs base:** each copy file is matched to a base path by path suffix. The copy's repo directory is learned from
   byte-identical files ("dominant prefix"), so `src/index.js` of a monorepo package can't be paired with an unrelated
   `index.js`. Then: same / differ / copy-only.
4. **History check:** for each differing file, is the copy's exact blob somewhere in the base's history of that path?
   - Yes: an *older* revision, so harmless.
   - No: *post-base* content (a newer release, a post-fix build, or a fork). That's the leak signal.
5. **Hidden-test identifiers:** identifiers that the hidden test patch adds (excluding the harness script) and that
   occur nowhere in the base tree are grepped across every untracked file. Generic words are dropped by document
   frequency: a "rare" token occurs in 5 files or fewer. This catches caches and build outputs that no name matches.
6. **Second checkouts:** `.git` dirs and directories with the project's root manifest outside the repo.
7. **Repo bytecode:** `__pycache__/*.pyc` with no source (orphan) or whose header mtime/size doesn't match the source (stale).
8. Repo mtime clustering (informational).

**Transplant test (confirmation without a gold patch):** copy the post-base files of the installed copy over the repo,
then run the task's real verifier. A pass means the copy contains a working fix, i.e. an agent could solve the task by
`cp` alone. All 21 positives score 0 under the unchanged verifier (nop control), so the pass is the copy's doing. A
fail does **not** clear a copy: unrelated version drift breaks blind copying (e.g. handlebars, scipy, fixieai, all of
which agents demonstrably used).

**Release dates:** for name-matched copies that aren't byte-close to base (published releases from other branches),
`residue_release_dates.py` looks up the release date on npm / PyPI / the Go proxy and compares it with the base commit
date. Example: handlebars 4.7.9 was released 2026-03-26, against a base of 2017-10-06, so it may carry a backport of the fix.

## Results

| Finding | Tasks | Notes |
|---|---|---|
| Installed copy with post-base files, genuine copy (most files equal base or older base revisions) | 47 | e.g. airflow 000405: 1 of 964 files differs (`models/taskinstance.py`, the fix); homeassistant 001507: `zha/sensor.py`, 1 of 5,138 |
| Published release of the project dated after base (not byte-close) | 17 | handlebars 001427, loopback 001815, karma 001678, uglify-js 001931, ... |
| Installed copy under another distribution name (manual) | 3 | 003058 `Zope`→OFS, 000195 `paddlepaddle`, 000255 theano |
| **Transplant-confirmed: the copy alone solves the task** | **21** | 000092 000322 000405 000406 000407 000466 000484 000497 000812 000880 001269 001839 002070 002308 002384 002429 002537 002668 002755 002829 002913 |
| Stale / orphan repo bytecode | 15 | MiMo's `git clean` keeps `lib/`, `vendor/`, `.build_env/`... e.g. 000242: compiled hidden test `test_polygon` + `crs.pyc` from another source version |
| Build cache / build output with hidden-test or fix symbols | 4 | 000556 Go build cache (`/opt/stage4a-go-build-cache`), 000699 Deno compile cache of the hidden test, 000046 compiled hidden test in `lib/test`, 002679 prebuilt `lib/futil-js.js` + newer `node_modules/futil` |
| Second checkout of the project | 2 | 002517, 002790: `/testbed` next to `/workspace/repo` |
| Toolchain-bundled newer release (not fixable by rewriting) | 3 | 002060 npm repo vs Node's bundled npm 10/11; 002061 undici; **002070 nopt (transplant-confirmed)** |

The six tasks of `leak_flagged.txt` (trajectory audit) are all among them. The scan finds about 14× more contaminated
images than the trajectory audit could see.

Not leaks (checked by hand): copies identical to base or older revisions (OLDER / SAME); separate dependency projects
whose API the tests use (nuplan 001219, ase 000233, pydeconz 001489, op-geth 000901, bamboo-pipeline 000253); vendored
third-party libraries (000437 protobuf / pybind11).

## Fixes

`mimo_to_harbor.py --residue-fixes-file residue_fixes.tsv` appends step 6 to the `mimo_setup.sh` of the listed tasks
only, so the other images and their caches are untouched. The actions on 82 tasks are generated by
`residue_fixes_build.py` from the pass outputs, plus a hand-verified `MANUAL` list:

| Action | Tasks | Effect |
|---|---|---|
| `sync <copy> <base_prefix>` | 61 | every copy file that base also has is rewritten to base's content and the copy's bytecode is dropped. Files that base lacks are kept (deleting them broke installs, see below). The install keeps its compiled parts and metadata, so imports keep working. |
| `syncfile` | 1 | same for a single-module install (000466 `pyupgrade.py`) |
| `rm <path>` | 9 | caches, build outputs, second checkouts, package-manager cache entries of post-base releases |
| `pycache` | 15 | delete `__pycache__` dirs in the repo (legacy sourceless `.pyc` are kept) |

Validation of the fixed images (`jobs/residue-fixed-probe2`): all build and set up. No genuine copy keeps post-base
files (66 copies before, 0 after) except the documented leftovers below. That probe ran the first version of `sync`, which also deleted post-base files. Six packages shrank as a result,
which led to the breakage fixed below.

**Leftovers, recommended exclusions:**
- 002070: the confirmed leak is Node's bundled `npm/node_modules/nopt`. Rewriting it would break npm. **Exclude.**
- 002607: scipy built from the fixed source (see the agent rerun). **Exclude.**
- 002060, 002061: newer npm / undici bundled with Node (12 years newer in 002060); not transplant-confirmed. Accept or exclude.
- 001920 (`@midwayjs/mock` 4.1.0, released 2026) and 001815's `repository-json-schema` copies: no safe layout mapping. Not confirmed.

## Agent rerun on the fixed images

`jobs/residue-rerun-v1`: mini-swe-agent, the same config as the scored run, 4 attempts on 82 fixed tasks (002070
excluded). It served from node 4's four TP=2 engines through `affinity_router` (`backends_n4.txt`) and the Cloudflare
tunnel, at 100 concurrent trials. Baseline for the same tasks in the scored run: pass@1 0.376, pass@4 0.682.

Three image versions were run; each task is scored on its final one (`residue-rerun-v1`, then `-v2` for the 22 tasks
whose sync had deleted files, then `-v3` for the 15 bytecode tasks). The control is 120 random tasks that weren't
fixed, run the same way (`jobs/residue-control-v1`, `-v1b`), to separate run-to-run drift from the fixes. Changes are
paired per task, with 95% bootstrap intervals.

| Tasks | n | pass@1 scored run | pass@1 rerun | Change [95% CI] | pass@4 scored run | pass@4 rerun |
|---|---|---|---|---|---|---|
| fixed (82) | 82 | 0.369 | 0.293 | -0.076 [-0.137, -0.015] | 0.671 | 0.537 |
|   transplant-confirmed | 20 | 0.475 | 0.350 | -0.125 [-0.212, -0.025] | 0.850 | 0.700 |
|   other fixed | 62 | 0.335 | 0.274 | -0.060 [-0.137, +0.012] | 0.613 | 0.484 |
| control (unfixed) | 120 | 0.438 | 0.388 | -0.050 [-0.096, -0.004] | 0.683 | 0.633 |

**Fixed minus control, pass@1:  -0.026 [-0.103, +0.049]** (95% bootstrap); infra fixed=0 control=0.

How to read it:
- **The rerun setup itself scores lower:** the unfixed control drops 0.05. Possible causes are TP=2 engines instead of
  the scored run's TP=1 replicas, 4 engines instead of 32 so agent wall time is about 2× longer, and sampling. No
  attempt hit a limit: over 99% end in `Submitted`, step counts are unchanged and there were 0 infra failures.
- **Beyond that drift, the fixes cost −0.026 pass@1, which isn't significant.** Most fixed tasks are as solvable as
  before.
- **The transplant-confirmed group drops the most** (−0.125, about −0.075 beyond drift). That's consistent with some
  solves having leaned on the installed copy.

**Breakage found by the rerun, and fixed:**
1. Deleting a copy's files that base lacks broke two installs. 002607 lost the generated `scipy/__config__.py`, and
   000418 lost airflow modules that other installed packages import. `sync` now only rewrites files to base and never
   deletes (v2).
2. The bytecode rule also deleted legacy sourceless `.pyc` files, which Python imports as modules (000225's
   `linopy/version`). `pycache` now removes only `__pycache__` dirs (v3).

After these, the reruns show no import or module errors that the scored run didn't also have. The one
remaining `linopy.version` error came from an agent that ran `rm -f linopy/version.py` itself.

**Applied:** the 82 fixed `mimo_setup.sh` files are now in `harbor_tasks/mimo-code`. The originals are backed up in
`~/mimo/residue/backup_mimo_setup_20261009/`. Only those files changed, and every `task.toml` is unchanged. Regenerate
with the usual flags plus `--residue-fixes-file mimo_swe/residue_fixes.tsv`.

**Exclude** (`residue_excluded.tsv`): 002070 (unfixable, confirmed leak in Node's bundled `nopt`) and 002607 (the
installed scipy was built from the fixed source and can't be reset to base). The six `leak_flagged.txt` tasks other
than 002607 are fixed and can be re-admitted.

## Also found: tasks that can't run on Daytona at their provisioned size

Daytona caps a sandbox at **16 GB memory and 20 GB disk**. `task_resources.tsv` gives 8 tasks 17 GiB (000348 000604
000776 000876 000921 002019 002361 002502), 001108 40 GiB of disk and 001540 22 GiB. Every create for them fails with
`Memory/Disk request exceeds maximum allowed per sandbox`. The residue probes ran them at 8 GiB / 20 GiB.

## Files

- Code (`rl-env-cleaner/mimo_swe/`):
  - `residue_probe.sh`: the probe;
  - `residue_audit.py`: build probe / transplant task copies; parse;
  - `residue_triage.py`: per-task scores from pass 1;
  - `residue_classify.py`: copy classes;
  - `residue_release_dates.py`: registry release dates;
  - `residue_fixes_build.py`: generates the fix list;
  - `mimo_to_harbor.py`: `RESIDUE_FIX_HEAD`, `residue_fix_sh`, `--residue-fixes-file`.
- Data (`~/mimo/residue/`):
  - `triage_all.tsv`, `copies_p3.tsv`, `release_dates.tsv`;
  - `transplant_pass.txt`, `residue_fixes.tsv` (also committed as `mimo_swe/residue_fixes.tsv`), `final_compare.txt`;
  - `harbor_tasks_staging/mimo-code` (the regenerated tasks; now identical to `harbor_tasks/mimo-code`).
- Reproduce a pass: `python3 residue_audit.py make <dir> @tasks.txt`, then
  `AGENT=nop N_CONCURRENT=100 MIMO_RUN_ID=residue-audit run_harbor.sh <dir> <job>`.
