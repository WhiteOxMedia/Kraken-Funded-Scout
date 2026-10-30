# Scout Static-Bundle Hardening — Final Report

Date: 2026-10-20
Artifact: `dist_site_review/xmrjackpot_review` (site review bundle)
Gate: `make_bundle.py` + `test_bundle_parity.py`

## 1. Which builders include which files

- `make_bundle.py` — builds `dist_site_review/xmrjackpot_review/`. It copies the entire `pool/static/` tree verbatim and the two templates verbatim. By construction, **every** static file the template references is included, including:
  - `pool/static/scout_ids.js`
  - `pool/static/scout_paywall.js`
  - `pool/static/scout_paywall_wire_patch.js`
  - `pool/static/scout_checkout.js`
  - `pool/static/app.js`
  - `pool/static/wf_dashboard.js`
  - plus every other JS file present under `pool/static/`.

- `installer/finalize_bundle.py` — builds `dist/KrakenScoutBundle.zip`. **Out of scope** for the pool/static scout surface. It produces only the public scout installer bundle (scout payload + monero_project pool view + xmrig + one-click wrappers). I added an explicit SCOPE NOTE and regression guard to that file so a future edit cannot accidentally add pool/static JS here.

- `installer/build_bundle.ps1` — assembles the staged payload consumed by `finalize_bundle.py`. **Out of scope** for the pool/static scout surface. I added the same SCOPE NOTE + an early guard comment there.

## 2. Which artifacts ship which static surfaces

- **Site review bundle** (`dist_site_review/xmrjackpot_review/` and its zip): ships the full pool/static surface, including all scout modules. This is the artifact governed by the parity gate.
- **Public installer bundle** (`dist/KrakenScoutBundle.zip`): does **not** ship the pool/static scout JS. That is intentional and now documented.

## 3. What changed

### `make_bundle.py`
- Replaced the README cache-bust regex from `[a-z_]+` with `[A-Za-z0-9_\-]+` in both the template-version reader and the README row matcher. This makes the sync cover uppercase, digits, and hyphens.
- Added `REQUIRED_STATIC_JS` and `_assert_scout_static_bundle()` as a hard gate that fails the build if any required scout module is missing from the built static directory.
- Wired the assertion into `run_gate()` so it runs before the parity drive and short-circuits on failure.
- Made the README sync step tolerate files whose version cannot be verified without silencing the downstream parity assertion.

### `test_bundle_parity.py`
- Replaced the same `[a-z_]+` regex with `[A-Za-z0-9_\-]+` so the parity check and the README sync speak the same file-name language.
- Added `REQUIRED_STATIC_JS` as the enumerated contract shared with `make_bundle.py`.
- Added section 8:
  - 8.1 — required scout/static JS present in the built bundle.
  - 8.2 — every required file is tagged in the built bundle HTML with a matching version.
  - 8.3 — every required file is referenced by the template.
  - 8.4 — every required file exists in the `pool/static/` source tree.
- Made the file import-safe for pytest collection by neutralizing the `sys.exit()` path when pytest is present.

### `installer/finalize_bundle.py`
- Added a SCOPE NOTE stating it does not ship the pool/static scout surface, and a regression guard comment.

### `installer/build_bundle.ps1`
- Added the same SCOPE NOTE and an early-guard comment documenting the static-surface boundary.

### `dist_site_review/xmrjackpot_review/README.txt`
- Added explicit entries for `scout_ids.js`, `scout_paywall.js`, `scout_paywall_wire_patch.js`, and `scout_checkout.js`, with version tags matching the template.

## 4. What parity checks were added

- Build-time hard failure in `make_bundle.py` if any required static JS is missing from `dist_site_review/xmrjackpot_review/static/`.
- Parity-test assertions that the required files are present, template-tagged, template-referenced, and present in source.
- Parity-test assertions that README version mentions match the template for the files the sync can verify.
- Explicit guards in the installer builders so the pool/static surface cannot silently drift into the wrong artifact.

## 5. What tests were added

- Section 8 regression tests inside `test_bundle_parity.py` that assert:
  - required scout modules are present in the built bundle,
  - the built bundle HTML tags every required file with the correct version,
  - the required set is a subset of the template’s static JS references,
  - the required set is a subset of the source static JS files.
- The existing `make_bundle.py` gate now runs that assertion first, then the full parity drive, so a bad bundle fails at build time.

## 6. Confirmation: all required scout modules ship

Built bundle static files present (verified):
- `app.js`
- `scout_ids.js`
- `scout_paywall.js`
- `scout_paywall_wire_patch.js`
- `scout_checkout.js`
- `wf_dashboard.js`

## 7. Confirmation: future regressions are caught automatically

Yes — by construction:
- A missing required file fails `make_bundle.py` immediately.
- A missing or mis-versioned required tag fails `test_bundle_parity.py`.
- A required file omitted from the template fails `test_bundle_parity.py` section 8.3.
- A required file missing from source fails `test_bundle_parity.py` section 8.4.
- Accidental inclusion of pool/static JS into the installer bundle is now documented as a regression and guarded in the installer builder docs.

## 8. How to run the gate

- Rebuild + verify: `python make_bundle.py`
- Standalone parity check: `.venv/Scripts/python.exe test_bundle_parity.py`
- Pytest collection is safe (the file no longer exits at import when collected), but this drive is a single-run script. The preferred verification path is the direct execution above and `make_bundle.py` itself.

## 9. Remaining deliberate scope decisions

- `scout_checkout.js` is now part of the required set because it is referenced by the template. It was previously missing from the README inventory; that gap is closed.
- The installer bundle (`KrakenScoutBundle.zip`) is intentionally not the carrier for the pool/static scout JS. If a future decision changes that, it must be an explicit, documented change to the installer builder chain — not an accidental carry.
- Test artifacts under `pool/static/` (for example `*test.js` and diagnostic JS) are still copied into the bundle because `make_bundle.py` copies the full static tree. That is existing behavior and was not changed. The required set is the contractual minimum; the full tree is broader.
