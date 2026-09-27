"""verify_hostinger_corrections.py — THE CORRECTIONS GATE (Sep 25).

Grades Hostinger's corrected files against the repo's standing
guarantees BEFORE they touch production:

  1. TEXT PRESERVATION — every visible string of the CURRENT
     production HTML must survive in the corrected file (tag-stripped,
     entities unescaped). Wording is contractual; corrections restyle,
     they do not rewrite.
  2. LINK/ROUTE SAFETY — every href/src in the current HTML must
     survive (cache-busters ?v=N normalized). No route may disappear.
  3. HOOK SAFETY — every id=, name=, and data-page attribute app.js /
     wf_dashboard.js / push_client.js address must still exist in the
     corrected HTML (JS files are compared byte-exact: if they were
     changed, EVERY id they reference is re-verified against the
     corrected HTML).
  4. SERVICE-WORKER SCOPE — /push_sw.js must remain the registration
     URL (the root-scope fix).
  5. DESIGN-SYSTEM PINS — radius 0 (flat), the orange/slate/gray
     tokens, and the wf-* workflow classes survive in corrected CSS.

Usage:
  python verify_hostinger_corrections.py <corrected_dir>

  <corrected_dir> must contain corrected versions of any of:
  index.html, admin_panel.html, static/style.css, static/app.js,
  static/wf_dashboard.js, static/push_client.js, static/push_sw.js

Exit 0 = every check passed (safe to apply). Exit 1 = findings listed.
House conventions: check(n, cond, why), RESULT line.
"""
import html as html_mod
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROD = HERE / "pool"
passed = failed = 0


def check(n, cond, why=""):
    global passed, failed
    print(f"{'PASS' if cond else 'FAIL'}  {n}  {why}")
    passed, failed = passed + (1 if cond else 0), failed + (0 if cond else 1)


def visible_strings(doc):
    doc = re.sub(r"<script\b.*?</script>", " ", doc, flags=re.S | re.I)
    doc = re.sub(r"<style\b.*?</style>", " ", doc, flags=re.S | re.I)
    doc = re.sub(r"<!--.*?-->", " ", doc, flags=re.S)
    text = re.sub(r"<[^>]+>", "\n", doc)
    text = html_mod.unescape(text)
    return {ln.strip() for ln in text.splitlines() if len(ln.strip()) >= 3}


def links_of(doc, counts=False):
    """The doc's links. As a SET (deduped, cache-busters stripped) for
    display; as a COUNTER when counts=True - a correction that drops one
    occurrence of a repeated link (a removed Kraken CTA) must fail the
    route check even when the URL survives elsewhere (review triage,
    Sep 25 evening V)."""
    found = [re.sub(r"\?v=\d+", "", l)
             for l in re.findall(r'(?:href|src)="([^"]+)"', doc)]
    if counts:
        import collections
        return collections.Counter(found)
    return sorted(set(found))


def ids_of(doc):
    return set(re.findall(r'id="([^"]+)"', doc))


def main() -> int:
    corr = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
    if not corr or not corr.is_dir():
        print("usage: python verify_hostinger_corrections.py <corrected_dir>")
        return 1

    # WHAT MAY BE CORRECTED (review triage, Sep 26): the docstring's
    # contract is "corrected versions of ANY OF" the listed files - a
    # correction that touches only the admin panel is legitimate. The
    # old entry demanded index.html unconditionally, which refused such
    # sets ungraded; with no corrected index, production index.html is
    # graded against ITSELF (a structural no-op), and per-file sections
    # skip what is not supplied.
    cur_html = (PROD / "templates" / "index.html").read_text(encoding="utf-8")
    new_path = corr / "index.html"
    if not new_path.exists():
        supplied = sorted(p.name for p in corr.rglob("*") if p.is_file())
        if not supplied:
            print("no corrected files found - nothing to grade")
            return 1
        print("no corrected index.html - grading index against itself "
              f"(corrected set: {supplied})")
    # no corrected index -> production graded against itself (no-op)
    new_html = (new_path.read_text(encoding="utf-8")
                if new_path.exists() else cur_html)

    # corrected-vs-production JS (built early: sections 2 and 3 both use it)
    js_sources = {}
    for js in ("app.js", "wf_dashboard.js", "push_client.js", "push_sw.js"):
        corr_js = corr / "static" / js
        js_sources[js] = (corr_js.read_text(encoding="utf-8")
                          if corr_js.exists()
                          else (PROD / "static" / js).read_text(encoding="utf-8"))

    print("== 1. TEXT PRESERVATION ==")
    old_text = visible_strings(cur_html)
    new_text = visible_strings(new_html)
    missing = sorted(s for s in old_text if s not in new_text)
    check(1.1, not missing,
          f"all {len(old_text)} production visible strings survive "
          + (f"- MISSING {len(missing)}: {missing[:8]}" if missing else ""))

    print("== 2. LINK/ROUTE SAFETY ==")
    old_links = links_of(cur_html, counts=True)
    new_links = links_of(new_html, counts=True)
    gone = sorted((old_links - new_links).elements())
    check(2.1, not gone,
          f"all {sum(old_links.values())} production link occurrences "
          f"({len(old_links)} distinct) survive "
          + (f"- GONE: {gone[:8]}" if gone else ""))
    check(2.2, "/push_sw.js" in js_sources.get("push_client.js", "")
          and 'getRegistration("/")' in js_sources.get("push_client.js", ""),
          "the service worker is still registered from the ROOT path "
          "(the /static/ scope fix must not regress)")

    print("== 3. HOOK SAFETY ==")
    new_ids = ids_of(new_html)
    cur_ids = ids_of(cur_html)
    changed_js = [j for j, src in js_sources.items()
                  if src != (PROD / "static" / j).read_text(encoding="utf-8")]
    check(3.1, True, f"corrected JS changed: {changed_js or 'none (byte-exact)'}")
    referenced = set()
    created = set()
    for src in js_sources.values():
        referenced |= set(re.findall(r'getElementById\("([^"]+)"\)', src))
        referenced |= set(re.findall(r"getElementById\('([^']+)'\)", src))
        referenced |= set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', src))
        referenced |= set(re.findall(r"\$\('#([A-Za-z0-9_-]+)'\)", src))
        # ids the JS itself injects at runtime (template strings) are
        # self-fulfilling - they answer to their creator, not the HTML
        created |= set(re.findall(r'id="([A-Za-z0-9_-]+)"', src))
        created |= set(re.findall(r"\.id\s*=\s*'([A-Za-z0-9_-]+)'", src))
        created |= set(re.findall(r'\.id\s*=\s*"([A-Za-z0-9_-]+)"', src))
    # review triage (Sep 25 evening V): grade EVERY referenced id against
    # the corrected ids - a corrected JS may reference a hook the
    # corrected HTML dropped, new or old alike. The id pool spans ALL
    # corrected HTML: app.js legitimately references ids that live in
    # the injected admin panel, not the index.
    _admin_corr = corr / "admin_panel.html"
    _all_corr_ids = set(new_ids) | created
    if _admin_corr.exists():
        _all_corr_ids |= ids_of(
            _admin_corr.read_text(encoding="utf-8"))
    # KNOWN-DEAD (review triage, Sep 25 evening V): #coinRain is a
    # vestige - app.js guards its absence (if (!container) return) and
    # no surface creates it, in production or in any correction. A
    # correction cannot lose what never existed.
    _known_dead = {"coinRain"}
    dangling = sorted(r for r in referenced
                      if r not in _all_corr_ids and r not in _known_dead)
    check(3.2, not dangling,
          f"all {len(referenced)} JS-referenced ids exist in the "
          f"corrected HTML "
          + (f"- DANGLING: {dangling[:10]}" if dangling else ""))
    lost_ids = sorted(cur_ids - new_ids)
    check(3.3, not lost_ids,
          f"no production id disappeared ({len(cur_ids)} ids) "
          + (f"- LOST: {lost_ids[:10]}" if lost_ids else ""))

    print("== 3b. CORRECTED ADMIN PANEL ==")
    admin_corr_path = corr / "admin_panel.html"
    if admin_corr_path.exists():
        cur_admin = (PROD / "templates" / "admin_panel.html").read_text(
            encoding="utf-8")
        new_admin = admin_corr_path.read_text(encoding="utf-8")
        _miss = sorted(s for s in visible_strings(cur_admin)
                       if s not in visible_strings(new_admin))
        check(3.4, not _miss,
              f"all {len(visible_strings(cur_admin))} admin-panel strings "
              f"survive " + (f"- MISSING: {_miss[:6]}" if _miss else ""))
        # review triage (Sep 26): occurrences, not just distinct URLs -
        # a correction that drops ONE occurrence of a repeated link
        # (a removed Kraken CTA) must fail here too, exactly like 2.1.
        _al = links_of(cur_admin, counts=True)
        _nl = links_of(new_admin, counts=True)
        _gone = sorted((_al - _nl).elements())
        check(3.5, not _gone,
              "all admin-panel link occurrences survive "
              + (f"- GONE: {_gone[:6]}" if _gone else ""))
        check(3.6, not (ids_of(cur_admin) - ids_of(new_admin)),
              f"no admin-panel id disappeared "
              f"({len(ids_of(cur_admin))} ids) "
              + (f"- LOST: {sorted(ids_of(cur_admin) - ids_of(new_admin))[:8]}"
                 if ids_of(cur_admin) - ids_of(new_admin) else ""))
    else:
        check(3.4, False,
              "corrected admin_panel.html MISSING - the file inventory "
              "must stay unchanged (the corrected set ships every file "
              "it corrects)")

    print("== 4. DESIGN-SYSTEM PINS (corrected CSS) ==")
    css_path = corr / "static" / "style.css"
    if css_path.exists():
        css = css_path.read_text(encoding="utf-8")
        _tok = re.search(r"--radius\s*:\s*0(?:px)?\s*;", css)
        _uses = len(re.findall(r"border-radius\s*:\s*var\(--radius\)", css))
        check(4.1, bool(_tok) and _uses >= 10,
              f"flat corners survive (the --radius token is "
              f"{'0' if _tok else 'NOT zero'} and is used {_uses}x)")
        # review triage (Sep 27): compare token VALUES, not names -
        # a corrected stylesheet that redefines --mono-orange to an
        # unrelated color kept every name and still passed the old
        # name-only pin, defeating the stated orange/surface pins.
        def _tok_vals(css_text, names):
            out = {}
            for name in names:
                m = re.search(re.escape(name) + r"\s*:\s*([^;]+);",
                              css_text)
                if m:
                    out[name] = m.group(1).strip().lower()
            return out
        _names = ("--mono-orange", "--surface-white", "--text")
        _corr_vals = _tok_vals(css, _names)
        _prod_vals = _tok_vals(
            (PROD / "static" / "style.css").read_text(
                encoding="utf-8"), _names)
        _moved = [n for n in _names
                  if _corr_vals.get(n) is not None
                  and _prod_vals.get(n) is not None
                  and _corr_vals[n] != _prod_vals[n]]
        _missing = [n for n in _names if n not in _corr_vals]
        check(4.2, not _moved and not _missing,
              "the token layer survives (VALUES compared against "
              "production, not just names)"
              + (f" - REDEFINED: "
                 + str([(n, _prod_vals.get(n), _corr_vals.get(n))
                        for n in _moved]) if _moved else "")
              + (f" - MISSING: {_missing}" if _missing else ""))
        check(4.3, "wf-section" in css and "wf-status" in css,
              "the wf-* workflow layer survives")
    else:
        check(4.1, True, "no corrected style.css — CSS untouched")

    print("== 5. NO SECRETS IN CORRECTED FILES ==")
    import re as _re
    hits = []
    for f in corr.rglob("*"):
        if not f.is_file() or f.suffix.lower() in (
                ".png", ".jpg", ".ico", ".woff", ".woff2"):
            continue
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pat in (r"BEGIN [A-Z ]*PRIVATE KEY", r"xmrjackpot2026",
                    r"api[_-]?key\s*[:=]\s*['\"][A-Za-z0-9]{8,}",
                    r"authorization['\"]?\s*[:=]\s*['\"][^'\"]{8,}"):
            if _re.search(pat, txt, _re.I):
                hits.append((f.name, pat))
    check(5.1, not hits,
          "no credentials/keys in the corrected files "
          + (f"- HITS: {hits[:5]}" if hits else ""))
    return 0


if __name__ == "__main__":
    rc = main()
    print(f"RESULT: {passed} passed, {failed} failed")
    # a refusal (nothing supplied / usage) must NEVER read as success:
    # rc 1 means the gate graded nothing, and that is not a pass
    sys.exit(rc or (0 if failed == 0 else 1))
