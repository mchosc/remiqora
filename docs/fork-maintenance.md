# Maintaining the Remiqora fork

The maintained fork is [mchosc/remiqora](https://github.com/mchosc/remiqora); upstream
is [inikolax/remiqora](https://github.com/inikolax/remiqora), originally created by
Nikolay Cherkashin. Keep the Remiqora name, clearly label the fork and preserve
upstream history and attribution. The **0.3.0-dev.0** snapshot is unreleased; no
release date or public installer is promised.

## Branches and upstream integration

- `origin` points to `mchosc/remiqora`; `upstream` points to `inikolax/remiqora`.
  Check `git remote -v` before pushing.
  The local push default is `origin`; the upstream push URL is disabled to
  prevent accidental publication to the original project.
- Fork `master` is the tested default branch. Use small `fix/…`, `feat/…` and
  `docs/…` topics, reviewed against `master` with applicable checks passing.
- Create `sync/upstream-<date-or-topic>` from fork `master`, fetch upstream and
  merge the selected upstream commit/branch into that sync branch. Review conflicts,
  migrations, engine pins, API contracts, security defaults and fork behavior;
  run checks before integrating the sync branch into `master`.
- Preserve shared history: do not rebase or force-push `master`. Do not reset a
  modified engine checkout to make an upstream integration appear clean.
- `master` requires the frontend, backend and desktop unit checks. Administrator
  enforcement is enabled; force pushes and branch deletion are disabled. A second
  person's approval is not required for this solo-maintainer fork, but checks and
  a final diff review are still required.
- Send focused, independently useful changes upstream from a branch based on
  upstream's current base. Include regression evidence and necessary context.
  Do not submit the fork's accumulated baseline as a single upstream PR.

## Required checks

[AGENTS.md](../AGENTS.md) defines engineering requirements;
[CI](../.github/workflows/ci.yml) defines the current automated checked scope.
Use CI's Python version (currently 3.12), a disposable environment and a clean
dependency install for release verification. FFmpeg/ffprobe must be available for CPU media tests; text checks
need the declared image dependencies and a usable font.

The following shell example is for macOS/Linux, from the repository root. On
Windows, set the same three environment variables to fresh temporary paths before
running the corresponding commands. Never point tests at a real library or an
installed Seed-VC engine: checkpoint placement can change the engine's symlink.

```sh
remiqora_test_root="$(mktemp -d)"
python3.12 -m venv "$remiqora_test_root/venv"
. "$remiqora_test_root/venv/bin/activate"
export REMIQORA_CONFIG="$remiqora_test_root/config.json"
export REMIQORA_DATA_DIR="$remiqora_test_root/library"
export SEED_VC_DIR="$remiqora_test_root/seed-vc"

python -m pip install -r backend/requirements-test.txt
python backend/scripts/generate_contracts.py --check
(cd backend && python -m unittest discover -s app -p '*_test.py' -v)
(cd frontend && npm ci && npm test && npm run build)
(cd desktop && npm ci --ignore-scripts && npm test)
```

Also run the **exact strict-mypy platform loop** listed in CI, from `backend/`.
It checks Linux, macOS and Windows typing paths; a check using only the local
platform can miss an error behind a platform guard. Its explicit module list is
intentional; it is not a whole-backend typing claim. Regenerate changed Pydantic contracts with
`python backend/scripts/generate_contracts.py`, then rerun the drift check. Never
hand-edit `frontend/src/api/generated.ts`.

Record the commit, tool versions, command exit results and artifact/log locations.
Unit tests use temporary data and mocked model boundaries; do not download GPU
weights or touch personal recordings as part of them. CPU CI does not verify
GPU inference, Windows installation, perceptual quality or the full setup flow.

## Backup and recovery

Before a large migration or baseline checkpoint, wait for active generation,
training, preparation and video jobs to finish. Back up the Git history and the
working sources, including untracked source files. Keep private `.env` files,
local Git configuration and the user's Remiqora configuration separately, outside
the public repository.

Take a consistent SQLite snapshot through SQLite's backup API or after stopping
all writers; copying only a live database file can omit WAL transactions. Copy
user audio, voice checkpoints, video artifacts/projects, and user training datasets
and LoRA outputs, including those stored outside the configured library. Record
the original paths and verify the copied files. Downloaded model caches and
recreatable environments may be excluded only when that exclusion is documented.

A filesystem clone is useful local recovery, but is not an off-device backup.
Maintain a separate private backup for irreplaceable recordings and checkpoints.
Restore to the recorded paths or use the app's documented data-folder migration;
database references can contain absolute paths. Test recovery on a copied library.

Fork installers have a distinct app identity. The default library folder retains
the Remiqora name for compatibility; choose separate data folders when testing
upstream and fork together, and never run both against one mutable library.

## Release checklist

1. Choose a candidate commit with green required checks. Review the final diff
   and dependencies; check for secrets, personal audio, weights, generated output
   and machine paths accidentally included in tracked files.
2. On each platform claimed for the candidate, perform a clean installation and
   a short real workflow using explicitly installed engine/model versions.
   Record OS, device, engine/model revisions, results and limitations. A successful
   installer build is not an installation or model-inference test.
3. Upgrade a copied existing library. Verify schema migrations, IDs, favorites,
   originals/cloned versions, export catalogs, voice artifacts and saved video
   projects. Verify backup recovery and interrupted/cancelled work. Preserve
   original data until replacement artifacts are complete.
4. Check playback/download agree on the selected audio version and format;
   exercise cancel, shutdown and reload. Evaluate representative held-out voice
   trials and short fixed-seed generated video shots for features claimed to work.
   Mark untested platforms/features explicitly rather than inferring support.
5. Review MIT attribution and the separate licenses/notices for packaged tools,
   engine code, model weights and example media. Keep models out of repository
   archives and installers unless their redistribution is explicitly reviewed.
6. Update version metadata and release notes consistently. Explain changes,
   migration/backup instructions, verified environments, unsigned-installer status
   where applicable, known defects and experimental features.
7. After maintainer approval, tag the reviewed candidate with a version matching
   `desktop/package.json`. Inspect the resulting installers and checksums in a
   **draft prerelease**. [Desktop packaging](../.github/workflows/desktop.yml)
   creates drafts for version tags; it does not establish runtime support.
   Packaging also runs the reusable CI checks first. Reruns may replace assets
   only while the release is a draft; published release assets are immutable.
8. Publish only after a maintainer reviews the actual draft, artifacts and recorded
   platform checks. A green build or an uploaded artifact is not publication approval.

Use manual packaging runs for experiments. Do not tag the development snapshot
just to obtain a public download, and do not promise a release while the required
runtime checks remain incomplete.

## Attribution and support

Preserve [LICENSE](../LICENSE), including Nikolay Cherkashin's original copyright,
and links to upstream. Clearly distinguish fork changes from upstream releases.
The MIT repository license covers repository contributions; model, dataset,
third-party-tool and media terms require separate review of the exact artifacts.

Fork support belongs in [fork issues](https://github.com/mchosc/remiqora/issues).
Follow [SECURITY.md](../SECURITY.md) for sensitive reports; do not put secrets or
private recordings in a public issue. Maintainers should document unresolved
limitations instead of treating an inherited platform badge as new evidence.
