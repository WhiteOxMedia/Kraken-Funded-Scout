# the CodeRabbit advisory gate (Sep 7): staged changes get a warn-only agent review before landing; CODERABBIT_OFF=1 skips, --no-verify always available

## what the gates do

- **pre-commit (CodeRabbit)**: reviews the staged diff before it lands; warn-only, fail-open; runtime churn stashed out of the review.
- **post-commit (Copilot)**: reviews the landed commit as a second opinion; warn-only, fail-open; churn filtered from the diff; COPILOT_OFF=1 skips, COPILOT_TIMEOUT_SEC adjusts the clock (default 240s).
- **Auth**: CodeRabbit needs an *agentic* API key (Settings → API Keys); Copilot needs `copilot login` (browser OAuth). Until each is signed in, its gate fail-opens with a one-line reason.
- **Skipping**: `git commit --no-verify` skips the pre-commit review only — the post-commit second opinion always runs (a post-commit hook cannot block, so there's nothing to skip); the env switches (`CODERABBIT_OFF=1`, `COPILOT_OFF=1`) skip one gate each. (The second opinion caught this very sentence's first draft claiming otherwise.)
# second-opinion marker
