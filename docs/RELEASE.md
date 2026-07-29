# Educational release checklist

Highland is MIT-licensed. All company, customer, employee, incident, ticket,
message, document, project, and metric records checked into `data/seed/` are
fictional educational data. Do not add real credentials, customer data,
proprietary prompts, or third-party copyrighted corpora.

Run the automated release gate:

```bash
make release-check
```

Before tagging an educational release, complete this checklist from a clean
clone:

- [ ] `git status --short` is empty and the checkout contains no `.env`.
- [ ] `docker compose up --build` reaches healthy status for the web, API,
      catalog, and six mock services.
- [ ] `curl http://localhost:8080/health` reports `scripted`.
- [ ] The four exercises in [the learning path](LEARNING_PATH.md) pass without
      an API key.
- [ ] Discover, Create, the approval pause/resume, and Automation are usable at
      `http://localhost:3000`.
- [ ] `highland backup export /tmp/highland-learning-state.zip`, reset, and
      `highland backup import /tmp/highland-learning-state.zip --replace`
      restore a human-created artifact, workflow, and trace.
- [ ] `highland reset --yes` restores mock state and clears only the displayed
      platform targets; `data/seed/` remains unchanged.
- [ ] `make ci` passes without provider secrets.
- [ ] The three scenario manifests in `data/scenarios/` pass their deterministic
      evaluations.
- [ ] If making a live-demo claim, review `config/model_prices.json`, set local
      and Cohere dashboard budgets, then explicitly run
      `HIGHLAND_RUN_LIVE_TESTS=1 COHERE_API_KEY=... highland eval all`.
- [ ] The README links the architecture, testing, scenarios, learning path,
      implementation plan, and this checklist.
- [ ] `LICENSE` and the fictional-data statement above match the release.

Live evaluation is optional, billable, and never implied by the deterministic
release gate. Record its model IDs, date, cost report, and any skipped scenario
when it is used as release evidence.
