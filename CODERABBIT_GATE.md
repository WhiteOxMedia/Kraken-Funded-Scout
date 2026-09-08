# the CodeRabbit advisory gate (Sep 7): staged changes get a warn-only agent review before landing; CODERABBIT_OFF=1 skips, --no-verify always available

## what the gates do

- **pre-commit (CodeRabbit)**: reviews the staged diff before it lands; warn-only, fail-open; runtime churn stashed out of the review.
- **post-commit (Copilot)**: reviews the landed commit as a second opinion; warn-only, fail-open; churn filtered from the diff; COPILOT_OFF=1 skips, COPILOT_TIMEOUT_SEC adjusts the clock (default 240s).
- **Auth**: CodeRabbit needs an *agentic* API key (Settings → API Keys); Copilot needs `copilot login` (browser OAuth). Until each is signed in, its gate fail-opens with a one-line reason.
- **Skip everything**: `git commit --no-verify` skips both; the env switches skip one.
# second-opinion marker
