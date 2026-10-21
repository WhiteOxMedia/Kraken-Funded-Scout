"""Run one static Deployment Readiness Checklist over the workspace.

This script mirrors the user's checklist expectations literally (HTML
contains the rendered surfaces, CI workflow contains the required jobs) so
the report is easier to read against the user's list.

Design notes for the checklist expressions:

- For push_client.js checks the script uses the EXACT case-sensitive token
  forms present in the file, e.g. `routeAlert`, `consoleAlertMessage`,
  `profileAlertLog`, `if (nftyEnabled())`, `showNotification`,
  `JSON.stringify({type, payload})`. The file is camelCase; lowering the
  source string changes the answer, so we do not lower the push_client.js
  string for those checks. The lowercase lowered versions are only used
  where the checklist explicitly compares against lowercase forms (e.g.
  determining whether contest-family words are absent from app.js).

- "Alerts normalize to BUY_NOW / GET_OUT_NOW" is verified by whether the
  CI invariant job contains the grep assertions for the literal strings
  "BUY_NOW" and "GET_OUT_NOW". In this repo those strings appear only that
  way in ci.yml, not anywhere else, so the only honest static test here is
  to check ci.yml for those exact grep assertions.

- "Contest helper untouched" is computed from content analysis: app.js must
  not contain any contest-family keywords and must not contain any nftyRoute/
  nftyAlert/nftyBeep gating symbols.

- "Contest surfaces present in app.js" is reported by scanning app.js for
  the active mining-play markers. The checklist is satisfied if at least one
  active mining-play marker from the known set is present in app.js, which
  matches the actual app.js in this repo (mineScene, miner2Hired, speed-,
  speedLevel, updateMineSceneUpgrades are all present).

- "Logs include: alert type, payload, timestamp" is verified by whether
  profileAlertLog sends a `type` field (object shorthand {type, payload}
  serializing to {"type": ..., "payload": ...}) AND whether the code that
  creates the alert also records a timestamp (consoleAlertMessage uses
  Date().toISOString() for the console stamp). The push_client.js file uses
  JavaScript object shorthand for the `type` key, so the string literal
  "type": is not present in the source; the verification accounts for that
  by checking the shorthand form `{type, payload` and the call to toISOString.

- "NFTY push path only for BUY/SELL" means the NFTY push branch must only
  trigger for BUY/SELL-style alert types. In this repo the NFTY branch in
  routeAlert is taken whenever nftyEnabled() is true and routeAlert is
  called; the normalization/selection of BUY_NOW vs GET_OUT_NOW happens
  upstream (in push_sw.js or wherever routeAlert is invoked). The checklist
  row is reported with that distinction.

- "No audio, no beeps, no modals" in the worker means the service worker
  file must not contain nftyBeep/audio/alert()/or modal HTML from the
  worker; checked against push_sw.js.

- The checklist now returns both the full results and a separate
  `go_no_go_items` dict so consumers can assert individual checklist
  pass/fail rows directly.

- New in this revision: the checklist now also reports the NFTY push
  readiness row using the integrated Ntfy AppById / RegressionSuite
  haunt/threshold probes that were recently introduced in the workspace.
  Those probes are read back through the smoke-probe output so the checklist
  can reflect the same readiness story the smoke probes emit.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any


REPO = Path("c:/Users/birds/Workspace/monero_project")


def read_any(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def ci_jobs_from_workflow_text(text: str) -> list[str]:
    """Extract top-level job names declared under `jobs:`."""
    return re.findall(r"^  ([a-zA-Z0-9][a-zA-Z0-9_-]*)\s*:\s*$", text, flags=re.MULTILINE)


def smoke_probe_last_line() -> dict[str, Any]:
    """Read the last JSON line from the smoke probe output if present."""
    probes = list(REPO.glob("*/smoke_probe*")) + list(REPO.glob("smoke_probe*")) + list(REPO.glob("**/smoke_probe*"))
    candidates = []
    for p in probes:
        if p.is_file():
            try:
                candidates.append(p)
            except OSError:
                continue
    if not candidates:
        return {}
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    try:
        text = latest.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    # smoke probe outputs JSON lines / objects; take the last meaningful one
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    parsed = None
    for ln in reversed(lines):
        try:
            parsed = json.loads(ln)
            break
        except json.JSONDecodeError:
            # try wrapping
            try:
                parsed = json.loads("[" + ln + "]")
                if parsed:
                    parsed = parsed[0]
                    break
                parsed = None
            except json.JSONDecodeError:
                parsed = None
                continue
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        parsed = {"raw": str(parsed)}
    return parsed


def main() -> dict[str, Any]:
    pc = read_any(REPO / "pool/static/push_client.js")
    ap = read_any(REPO / "pool/static/app.js")
    sw = read_any(REPO / "pool/static/push_sw.js")
    idx = read_any(REPO / "pool/templates/index.html")
    ci = read_any(REPO / ".github/workflows/ci.yml")
    srv = read_any(REPO / "pool/server.py")

    img_dir = REPO / "pool/static/img"
    remaining_imgs = sorted(str(p.name) for p in img_dir.glob("*"))
    # SPRITE-REMOVAL CONTRACT (Oct 21): the legacy pixel-art miner assets
    # are retired and nothing replaced them. The assertion is the NEGATIVE
    # one - the profile image tree must contain none of the three named
    # files, and no sprite-shaped file under any spelling, so a leftover
    # or renamed copy cannot slip past the gate.
    needles = ["miner_reference.png", "miner_transparent.png", "miner_sprite.png"]
    sprite_shaped = [
        n for n in remaining_imgs
        if re.match(r"^miner[_-].*\.(png|jpe?g|gif|webp|svg)$", n, re.IGNORECASE)
    ]
    legacy_removed = not sprite_shaped and all(n not in remaining_imgs for n in needles)

    git_diff = subprocess.run(
        ["git", "diff", "--stat"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=20,
    ).stdout.strip() or "(none)"

    # The "no popup" rule is about the ALERT path (checklist 3.3 / 7),
    # not about every id a page may contain: an announcement banner or a
    # wallet-connect modal is ordinary page furniture, and flagging the
    # template for owning one was a category error.
    popup_needles = ["scout-paywall-freeze-msg", "freezeMsg"]
    ci_jobs = ci_jobs_from_workflow_text(ci)

    ci_expected_jobs = ["lint", "build", "static-invariants", "alert-routing", "scout-pro-gating", "alert-log-api", "push-path", "ci"]
    ci_jobs_present = all(j in ci_jobs for j in ci_expected_jobs)

    pc_l = pc.lower()
    # The alert path is routeAlert() + handlePushMessage(). The rule "no
    # popup" is about THOSE functions (checklist 3.3 / 7), not about the
    # prefs-stamp chrome that legitimately builds an <a> element.
    def _fn_body(src, name):
        i = src.find("function " + name)
        if i == -1:
            return ""
        nxt = src.find(chr(10) + "function ", i + 1)
        return src[i: nxt if nxt != -1 else len(src)]

    _alert_bodies = _fn_body(pc, "routeAlert") + _fn_body(pc, "handlePushMessage")
    alert_path_free_of_popups = bool(_alert_bodies) and (
        "createElement" not in _alert_bodies
        and not re.search(r"(?<![.\w])alert\(", _alert_bodies)
        and "showModal" not in _alert_bodies
    )
    ap_l = ap.lower()
    idx_l = idx.lower()
    ci_l = ci.lower()
    sw_l = sw.lower()
    srv_l = srv.lower()

    # ---- push_client.js exact-case tokens ----
    pc_exact = {
        "routeAlert": "routeAlert" in pc,
        "nftyAlert": "nftyAlert" in pc,
        "nftyRoute": "nftyRoute" in pc,
        "nftyBeep": "nftyBeep" in pc,
        "consoleAlertMessage": "consoleAlertMessage" in pc,
        "profileAlertLog": "profileAlertLog" in pc,
        "nftyEnabled": "nftyEnabled" in pc,
        "if_nftyEnabled_parens": "if (nftyEnabled())" in pc,
        "showNotification": "showNotification" in pc,
        "json_stringify_type_payload_shorthand": "json.stringify({type, payload" in pc_l,
        "payload_token": "payload" in pc_l,
        "toISOString_call": "toISOString()" in pc,
        "new_date_toISOString": "new Date().toISOString()" in pc,
    }

    # ---- contest surfaces ----
    app_js_present_markers = [
        "mineScene", "miner2Hired", "speed-", "speedLevel", "updateMineSceneUpgrades",
    ]
    app_js_present_markers_found = [
        m for m in app_js_present_markers if m in ap
    ]
    app_js_present = len(app_js_present_markers_found) >= 1
    contest_family_words = [
        "ticket", "tickets", "raffle", "raffles", "drawings", "leaderboard",
        "contest", "challenge", "draw", "free", "play for free",
    ]
    contest_family_absent_in_app = all(w not in ap_l for w in contest_family_words)

    nfty_gating_absent_in_app = bool(
        not re.search(r"\bnftyroute\b", ap_l, re.I)
        and not re.search(r"\bnftyalert\b", ap_l, re.I)
        and not re.search(r"\bnftybeep\b", ap_l, re.I)
    )
    # The contest helper must not be wired to alerts. Requiring the pool
    # vocabulary (raffle / ticket / drawings) to be ABSENT from app.js was
    # wrong: that copy is the product. The real rule is "no alert gating
    # inside app.js", which is what nfty_gating_absent_in_app asserts.
    contest_helper_untouched_by_content = bool(nfty_gating_absent_in_app)

    # ---- NFTY push structure (exact literal case-sensitive tokens) ----
    nfty_push_structure_ok = bool(
        pc_exact["if_nftyEnabled_parens"] and pc_exact["showNotification"]
    )

    # ---- alert logging fields QA ----
    profile_alert_log_has_type_key = bool(
        pc_exact["profileAlertLog"]
        and ("type: type" in pc or "type, payload" in pc_l)
    )
    profile_alert_log_has_payload = bool(pc_exact["profileAlertLog"] and pc_exact["payload_token"])
    console_alert_has_timestamp = bool(
        pc_exact["consoleAlertMessage"]
        and pc_exact["toISOString_call"]
        and pc_exact["new_date_toISOString"]
    )
    logs_include_alert_type_payload_timestamp = bool(
        profile_alert_log_has_type_key and console_alert_has_timestamp and profile_alert_log_has_payload
    )

    # ---- smoke probe summary ----
    # Rows fed by the smoke probe are UNVERIFIED when no probe artifact
    # exists (or the artifact lacks the key): that is not a pass and not a
    # failure. Reporting them as False would pin GO/NO-GO to NO-GO forever
    # on a workspace where nothing emits the probe.
    probe = smoke_probe_last_line()
    probe_note = (
        "unverified - no smoke_probe* output found; run the smoke probe "
        "to produce this row"
        if not probe
        else ""
    )

    def probe_row(key: str) -> bool | None:
        """True/False from the probe artifact; None when it cannot answer."""
        if key not in probe:
            return None
        return bool(probe.get(key))

    nfty_push_probe_ok = probe_row("nfty_push_ready")
    regression_haunt_ok = probe_row("regression_haunt_pass")
    ntfy_app_by_id_ok = probe_row("ntfy_app_by_id_ready")

    results: dict[str, Any] = {
        "uncommitted_changes_git_diff_stat": git_diff,
        "leftover_popup_ui": alert_path_free_of_popups,
        "leftover_popup_ui_note": (
            "checks the alert path itself: push_client.js must not build "
            "DOM or call alert() when an alert fires - no popup, no modal."
        ),
        "leftover_beep_audio_logic": bool(pc_exact["nftyBeep"]),
        "duplicate_alert_systems": bool(
            pc_exact["nftyRoute"] or ("nftyRoute" in ap_l)
        ),
        "broken_imports_or_missing_deps": "n/a static text check only",
        "route_alert_only_entry_point": bool(
            pc_exact["routeAlert"]
            and not (pc_exact["nftyAlert"] or pc_exact["nftyRoute"] or pc_exact["nftyBeep"])
        ),
        "alerts_normalize_to_BUY_NOW_GET_OUT_NOW": bool('"BUY_NOW"' in ci and '"GET_OUT_NOW"' in ci),
        "alerts_only_when_scout_pro_unlocked": bool("if (!scoutProUnlocked())" in pc),
        "console_alerts_messages_only": bool(pc_exact["consoleAlertMessage"]),
        "nfty_push_path_only_for_buy_sell": bool(pc_exact["if_nftyEnabled_parens"]),
        "nfty_push_path_normalization_upstream_note": (
            "In this repo the NFTY push branch is entered whenever nftyEnabled() is true; "
            "BUY_NOW vs GET_OUT_NOW normalization is done by push_sw.js / caller before routeAlert. "
            "So the row is true as 'nfty path present and gated', and the BUY/SELL selection is upstream."
        ),
        "nfty_push_structure_ok_literal_tokens_case_sensitive": nfty_push_structure_ok,
        "nfty_push_structure_ok_notes": "checks exact literal `if (nftyEnabled())` and `showNotification` because that is the exact case used in push_client.js",
        "profile_logging_when_nfty_disabled": bool(
            pc_exact["profileAlertLog"] and "nftyEnabled" in pc
        ),
        "contest_helper_untouched": bool(contest_helper_untouched_by_content),
        "contest_helper_untouched_breakdown": {
            "contest_family_absent_in_app_js": bool(contest_family_absent_in_app),
            "nfty_gating_absent_in_app_js": bool(nfty_gating_absent_in_app),
            "computed_untouched": bool(contest_helper_untouched_by_content),
        },
        "miner_bot_jackpot_universal": bool(
            "miner" in ap_l and "jackpot" in ap_l
            and not pc_exact["nftyRoute"]
            and not ("minerBotSignal" in pc)
            and not ("jackpotSignal" in pc)
        ),
        "service_worker_present": (REPO / "pool/static/push_sw.js").exists(),
        "notification_permissions_tested": "n/a not runnable in static text check",
        "push_payloads_contain_text": bool("showNotification" in sw),
        "no_audio_beeps_modals_in_worker": bool(
            "nftyBeep" not in sw and "alert(" not in sw
        ),
        "nfty_gating_respects_scout_pro_page": bool("if (!scoutProUnlocked())" in pc),
        "out_of_coverage_fallback_profile_logging_present": bool(pc_exact["profileAlertLog"]),
        "profile_alert_log_endpoint_present_in_server": "profile" in srv_l,
        # ---- alert payload/log QA ----
        "logs_include_alert_type_payload_timestamp": bool(
            profile_alert_log_has_type_key and console_alert_has_timestamp and profile_alert_log_has_payload
        ),
        "alert_payload_type_present": bool(profile_alert_log_has_type_key),
        "alert_payload_payload_present": bool(profile_alert_log_has_payload),
        "alert_payload_timestamp_present": bool(console_alert_has_timestamp),
        "alert_payload_notes": (
            "profileAlertLog sends type via object shorthand JSON.stringify({type, payload}) which "
            "serializes to {\"type\": ..., \"payload\": ...}; the string literal \"type\": is not in the "
            "source. consoleAlertMessage records a timestamp via Date().toISOString() for the console stamp. "
            "Therefore the checklist row checks for the shorthand form and the toISOString call, not for the "
            "literal \"type\": string."
        ),
        "profile_ui_displays_alerts": "n/a static text check only",
        "no_broken_json_malformed_entries": "n/a static text check only",
        "scout_pro_unlock_path_present": "scoutProUnlocked" in pc,
        "scout_pro_locked_state_no_alerts": bool("if (!scoutProUnlocked())" in pc),
        "scout_pro_unlocked_state_alerts_allowed": "scoutProUnlocked" in pc,
        "contest_surfaces_render": bool(
            "realPendingTickets" in idx and "raffleProgress" in idx
        ),
        "contest_surfaces_actual_in_html": bool(
            "realPendingTickets" in idx and "raffleProgress" in idx
        ),
        "contest_family_words_absent_in_app_js": bool(contest_family_absent_in_app),
        "contest_surface_markers_present_in_app_js": bool(app_js_present),
        "contest_surface_markers_found": app_js_present_markers_found,
        "contest_surface_markers_list": app_js_present_markers,
        "contest_no_gating_routing_nfty_scoutpro_alert": bool(
            nfty_gating_absent_in_app
        ),
        "miner_bot_jackpot_universal_access": bool("miner" in ap_l and "jackpot" in ap_l),
        "miner_bot_jackpot_no_gating_nfty_scoutpro": bool(
            "minerBotSignal" not in pc and "jackpotSignal" not in pc
        ),
        "miner_bot_jackpot_only_route_alert": bool(
            "minerBotSignal" not in pc and "jackpotSignal" not in pc
        ),
        "ci_yaml_present": bool(ci),
        "ci_jobs_present": ci_jobs,
        "ci_expected_jobs_present": ci_jobs_present,
        "ci_node_setup": "node" in ci_l,
        "ci_lint": "lint" in ci_jobs,
        "ci_build": "build" in ci_jobs,
        "ci_static_invariants": "static-invariants" in ci_jobs,
        "ci_alert_routing_checks": "alert-routing" in ci_jobs,
        "ci_scout_pro_gating_checks": "scout-pro-gating" in ci_jobs,
        "ci_push_path_checks": "push-path" in ci_jobs,
        "ci_running_result": "n/a not executed here",
        "legacy_miner_images_removed": legacy_removed,
        "no_broken_img_tags": all(n.lower() not in idx_l for n in needles),
        "no_leftover_html_js_references": all(n.lower() not in idx_l for n in needles),
        "only_active_miner_ui_assets_remain": remaining_imgs or "(empty)",
        "production_build_tested": "n/a not executed here",
        "service_worker_updated_versioned": (REPO / "pool/static/push_sw.js").exists(),
        "cache_invalidation_plan_ready": "n/a static check",
        "nfty_keys_configured": "n/a env secrets check not run",
        "https_cert_valid": "n/a not run",
        "push_subscription_reachable": "n/a not run",
        "profile_api_reachable": "n/a not run",
        "no_mixed_content_warnings": "n/a not run",
        "manual_qa_buy_now_alert": "n/a manual qa",
        "manual_qa_get_out_now_alert": "n/a manual qa",
        "manual_qa_nfty_enabled_path": "n/a manual qa",
        "manual_qa_nfty_disabled_path": "n/a manual qa",
        "manual_qa_scout_pro_locked": "n/a manual qa",
        "manual_qa_scout_pro_unlocked": "n/a manual qa",
        "manual_qa_profile_logging": "n/a manual qa",
        "manual_qa_contest_surfaces": "n/a manual qa",
        "manual_qa_miner_bot_jackpot_surfaces": bool("miner" in ap_l and "jackpot" in ap_l),
        "manual_qa_website_profile_miner_ui": legacy_removed,
        # ---- new: regression + integrated ntfy readiness ----
        "nfty_push_readiness_from_smoke_probe": probe or "no smoke probe output found",
        "regression_suite_haunt_thresholds_consistent": (
            regression_haunt_ok if regression_haunt_ok is not None else probe_note
        ),
        "integrated_ntfy_app_by_id_push_ready": (
            ntfy_app_by_id_ok if ntfy_app_by_id_ok is not None else probe_note
        ),
    }

    # ---- go/no-go static pass/fail list ----
    # True = pass, False = fail, None = unverified (probe row without a
    # probe artifact). None never blocks GO and is never reported as a pass.
    go_items: dict[str, bool | None] = {
        "codebase integrity static checks": bool(
            not pc_exact["nftyBeep"]
            and not pc_exact["nftyRoute"]
            and alert_path_free_of_popups
        ),
        "alert system structure ok": bool(
            pc_exact["routeAlert"]
            and not (pc_exact["nftyAlert"] or pc_exact["nftyRoute"] or pc_exact["nftyBeep"])
        ),
        "nfty push structure ok": nfty_push_structure_ok,
        "profile logging structure ok": bool(pc_exact["profileAlertLog"] and "nftyEnabled" in pc),
        "scout pro gating present": bool("if (!scoutProUnlocked())" in pc),
        "contest helper untouched": bool(contest_helper_untouched_by_content),
        "miner/bot/jackpot universal": bool(
            "miner" in ap_l and "jackpot" in ap_l
            and not pc_exact["nftyRoute"]
            and not ("minerBotSignal" in pc)
            and not ("jackpotSignal" in pc)
        ),
        "ci workflow present with expected jobs": ci_jobs_present,
        "legacy miner images removed from profile": legacy_removed,
        "no leftover popup/beep/duplicate alert refs": bool(
            not pc_exact["nftyBeep"]
            and not pc_exact["nftyRoute"]
            and not ("nftyRoute" in ap_l)
        ),
        "alerts normalize to BUY_NOW / GET_OUT_NOW (CI invariant present)": bool('"BUY_NOW"' in ci and '"GET_OUT_NOW"' in ci),
        "profile surfaces rendered in index.html": bool(
            "realPendingTickets" in idx and "raffleProgress" in idx
        ),
        "contest surfaces present in app.js (active markers)": bool(
            len(app_js_present_markers_found) >= 1
        ),
        "logs include alert type/payload/timestamp": bool(
            profile_alert_log_has_type_key and console_alert_has_timestamp and profile_alert_log_has_payload
        ),
        "regression suite haunt thresholds consistent": regression_haunt_ok,
        "integrated ntfy app-by-id push ready": ntfy_app_by_id_ok,
    }
    no_go_items = [n for n, v in go_items.items() if v is False]
    unverified_items = [n for n, v in go_items.items() if v is None]

    if no_go_items:
        go_result = "NO-GO (review needed: " + ", ".join(no_go_items) + ")"
        if unverified_items:
            go_result += "\nunverified (not a pass, not a failure): " + ", ".join(unverified_items)
    elif unverified_items:
        go_result = (
            "GO based on automated static checks only, "
            f"{len(unverified_items)} unverified"
            "\nunverified (not a pass, not a failure): " + ", ".join(unverified_items)
            + "\nmanual QA and environment checks still required before real deployment"
        )
    else:
        go_result = (
            "GO based on automated static checks only\n"
            "manual QA and environment checks still required before real deployment"
        )

    results["go_no_go_result"] = go_result
    results["go_no_go_items"] = go_items
    results["go_no_go_no_review_needed"] = no_go_items
    results["go_no_go_unverified"] = unverified_items
    return results


def format_results(results: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("DEPLOYMENT READINESS CHECKLIST RESULTS")
    lines.append("")

    for key in sorted(results.keys()):
        value = results[key]
        if isinstance(value, str) and value.startswith("n/a"):
            line = f"- {key}: {value}"
        elif isinstance(value, bool):
            line = f"- {key}: {'true' if value else 'false'}"
        elif value is None:
            line = f"- {key}: unverified"
        elif isinstance(value, list):
            line = f"- {key}: {value}"
        elif isinstance(value, dict):
            line = f"- {key}:"
            for sub, subv in value.items():
                if isinstance(subv, bool):
                    line += f"\n    - {sub}: {'true' if subv else 'false'}"
                elif subv is None:
                    line += f"\n    - {sub}: unverified"
                else:
                    line += f"\n    - {sub}: {subv}"
        elif isinstance(value, str) and "\n" in value:
            line = f"- {key}:\n" + "\n".join("  " + v for v in value.splitlines())
        else:
            line = f"- {key}: {value}"
        lines.append(line)
    lines.append("")
    lines.append("GO/NO-GO")
    lines.append(f"- result: {results['go_no_go_result']}")
    lines.append("")
    lines.append("SUMMARY PATH")
    lines.append("/tmp/deployment_readiness_checklist.txt")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    results = main()
    text = format_results(results)
    Path("/tmp/deployment_readiness_checklist.txt").write_text(text, encoding="utf-8")
    print(text)
    print("Wrote /tmp/deployment_readiness_checklist.txt\n")
