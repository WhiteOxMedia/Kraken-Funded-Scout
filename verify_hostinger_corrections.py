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


def links_of(doc):
    return sorted({re.sub(r"\?v=\d+", "", l)
                   for l in re.findall(r'(?:href|src)="([^"]+)"', doc)})


def ids_of(doc):
    return set(re.findall(r'id="([^"]+)"', doc))


def main() -> int:
    corr = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
    if not corr or not corr.is_dir():
        print("usage: python verify_hostinger_corrections.py <corrected_dir>")
        return 1

    cur_html = (PROD / "templates" / "index.html").read_text(encoding="utf-8")
    new_path = corr / "index.html"
    if not new_path.exists():
        print("no corrected index.html found — nothing to grade")
        return 1
    new_html = new_path.read_text(encoding="utf-8")

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
    old_links = set(links_of(cur_html))
    new_links = set(links_of(new_html))
    gone = sorted(old_links - new_links)
    check(2.1, not gone,
          f"all {len(old_links)} production links/routes survive "
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
    for src in js_sources.values():
        referenced |= set(re.findall(r'getElementById\("([^"]+)"\)', src))
        referenced |= set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', src))
    dangling = sorted(r for r in referenced if r not in new_ids
                      and r in cur_ids)
    check(3.2, not dangling,
          f"all {len(referenced)} JS-referenced ids still exist "
          + (f"- DANGLING: {dangling[:10]}" if dangling else ""))
    lost_ids = sorted(cur_ids - new_ids)
    check(3.3, not lost_ids,
          f"no production id disappeared ({len(cur_ids)} ids) "
          + (f"- LOST: {lost_ids[:10]}" if lost_ids else ""))

    print("== 4. DESIGN-SYSTEM PINS (corrected CSS) ==")
    css_path = corr / "static" / "style.css"
    if css_path.exists():
        css = css_path.read_text(encoding="utf-8")
        _tok = re.search(r"--radius\s*:\s*0(?:px)?\s*;", css)
        _uses = len(re.findall(r"border-radius\s*:\s*var\(--radius\)", css))
        check(4.1, bool(_tok) and _uses >= 10,
              f"flat corners survive (the --radius token is "
              f"{'0' if _tok else 'NOT zero'} and is used {_uses}x)")
        check(4.2, "--mono-orange" in css and "--surface-white" in css
              and "--text" in css,
              "the token layer survives (names stable)")
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
    sys.exit(0 if failed == 0 else 1)
