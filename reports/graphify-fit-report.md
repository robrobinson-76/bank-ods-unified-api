# Graphify Fit Assessment — Three Estate Archetypes

*Prepared for Rob Robinson · 2026-08-08*
*Sources: [graphify.com](https://graphify.com/), [Graphify-Labs/graphify](https://github.com/Graphify-Labs/graphify) README & docs (Apache 2.0, ~103k stars)*

> Graphify turns codebases into local, queryable knowledge graphs served to AI assistants over MCP. Code is parsed deterministically (tree-sitter AST, zero model calls); everything else is model-read. That one split — plus who pays tokens, and when — decides which of the three estates it fits.

A styled HTML version of this report lives alongside this file: [`graphify-fit-report.html`](./graphify-fit-report.html) (download and open in a browser).

---

## Verdicts at a glance

| Estate | Verdict | One-line reason |
|---|---|---|
| COBOL mainframe · 4–5M lines | 🔴 **Poor fit** | COBOL is not among the 36 tree-sitter grammars — no graph, or tens of millions of tokens for low-confidence inferred edges |
| 100-repo J2EE + microservices estate | 🟢 **Strongest fit** | The multi-repo global index was built for this; Java parses free; index dormant repos once, query impact across all 100 |
| Active monorepo (Java/Python) | 🟡 **Marginal — conditional** | Re-indexing is token-free but manual; under heavy churn a stale graph gives confidently wrong answers with citations |

---

## The token cost model

Token spend is not one number — it happens at three different moments, and only some file types incur it at all.

| Moment | What costs tokens | What's free |
|---|---|---|
| **Index time** | Non-code files only: docs, PDFs, SQL, Postgres schemas, Terraform, XML/config. Read by *your configured model* (comes out of your own API/assistant usage); edges tagged `INFERRED`. `--mode deep` spends more. | All code in the 36 supported languages (Java, Python, TS, Go, C#, Kotlin, Scala…): tree-sitter AST, on-device, **zero model calls**, edges tagged `EXTRACTED`. |
| **Re-index** | Only *changed* non-code files on `--update`. | Code re-scans, however frequent — CPU time, not tokens. |
| **Query time** | Graph query results entering the assistant's context — but these are compact paths with `file:line` citations. | Relative to the alternative, **this is where Graphify saves tokens**: one graph query replaces grepping and reading files across an estate — when that's even possible. |

**Net:** an estate that is *mostly supported code* indexes essentially free and pays for itself in cheaper queries. An estate that is *mostly unsupported code or config* pays real token bills for its most important edges — or gets no edges at all.

---

## Use case 1 — COBOL mainframe, 4–5M lines, rarely touched, impact checking

**Verdict: 🔴 Poor fit.** The profile sounds ideal — static system, one-time index, occasional high-stakes impact questions. The language support kills it.

**What works in its favor**

- **Static estate** — whatever index cost exists is one-time; the staleness problem barely applies.
- **On-device, no telemetry** — attractive for the compliance posture that usually surrounds mainframe code.
- **Surrounding artifacts** — it could still graph the documentation, runbooks, and interface specs *around* the mainframe.

**What breaks it**

- **COBOL is not a supported grammar.** The 36 tree-sitter languages are modern (the only legacy nod is regex-based Pascal/Delphi). COBOL files are either skipped — no graph, no value — or fall to model reading.
- **If model-read, the bill is enormous.** 4–5M lines is on the order of **50–75M input tokens per full pass** (rough estimate at ~12–15 tokens/line), for edges that are all `INFERRED` — the low-confidence kind.
- **Impact analysis here is safety-critical.** "Confidently cited but model-guessed" is precisely the failure mode you can't accept when checking blast radius on a system nobody dares touch.
- **Mainframe semantics are absent.** Copybooks, JCL job flows, CICS transactions, DB2 bindings — none of it is modeled. That's most of what "impact" means on this system.

**Recommendation:** Don't use Graphify here. This niche has purpose-built tooling — IBM ADDI / watsonx Code Assistant for Z, Rocket (Micro Focus) Enterprise Analyzer, CAST Imaging — that understands copybooks and JCL natively. If budget for those is the blocker, a bespoke parse (COBOL grammars exist outside tree-sitter) feeding a graph DB beats forcing this tool.

---

## Use case 2 — 100 repos: microservices + legacy J2EE, upgrade impact across connections

**Verdict: 🟢 Strongest fit.** Cross-repo impact analysis over a mostly-dormant estate is the single scenario where Graphify's design choices all point the right way — with one structural caveat about where J2EE keeps its wiring.

**Pros**

- **Purpose-built multi-repo workflow.** `graphify extract <repo> --global --as name` registers each repo into one cross-project index; `merge-graphs` combines graphs; `global list/remove` manages the fleet.
- **Java indexes free.** The bulk of the estate (legacy and microservice Java) is deterministic AST — the 100-repo index costs CPU, not tokens.
- **Dormant repos = index once.** Repos that never change never need re-scanning; the manual-update pain is near zero here.
- **Biggest query-time token savings of the three cases.** No assistant can grep 100 repos into context. One graph query answering "who calls this API / imports this artifact" replaces an exploration that's otherwise impractical, not just expensive.
- **Upgrade-planning queries are the native use.** `shortest_path`, `god_nodes`, dependency fan-out — the impact-across-connections question stated directly.
- **Citations keep it auditable.** Every claimed dependency comes with `file:line` and an `EXTRACTED`/`INFERRED` tag, so upgrade plans can be spot-verified.

**Cons**

- **J2EE hides its wiring in exactly the unsupported files.** `web.xml`, Spring XML, EJB descriptors, `pom.xml`, JSP — none are tree-sitter grammars. They're model-read (token cost) and produce `INFERRED` edges — and in old J2EE those files *are* the connection map.
- **Cross-service runtime edges aren't in any AST.** REST calls, JMS queues, shared databases connect services at runtime; the graph can only infer these from strings and config, or miss them.
- **One-time token bill for the config layer.** Modest per repo, but real across 100 XML-heavy legacy repos. At least it's one-time.
- **Scale limits.** The graph is a JSON file with a 512 MiB cap (overridable via `GRAPHIFY_MAX_GRAPH_BYTES`) loaded in memory — 100 repos may press against both.
- **You script the loop.** No "scan this folder of repos" command; a wrapper over 100 directories is on you.

**Recommendation:** Pilot before committing: index ~10 representative repos (mix of J2EE and microservice), then test against *one dependency chain you already know to be true* — ideally one that runs through XML config. If the inferred edges capture it, scale to the full 100 and make the graph the standard first step of every upgrade-impact assessment. If they don't, you've learned the tool sees your Java but not your wiring, at the cost of an afternoon.

---

## Use case 3 — single modern app, monorepo (Java or Python), heavy active development

**Verdict: 🟡 Marginal — conditional.** Token economics are fine here; the problem is operational. The graph is only as good as its last scan, and this is the one estate where the last scan is always going out of date.

**Pros**

- **Re-indexing costs zero tokens.** Java and Python are both fully deterministic; re-scan as often as you like for CPU time only. `--update` is incremental.
- **Architecture queries stay useful.** `god_nodes`, layering checks, boundary-violation detection, dead-code fan-in — better answered from an AST graph than from grep, and provable with citations.
- **Deep call-chain questions save context.** "Route → service → table" traces via one graph query instead of the assistant reading a dozen files into context.
- **Docs/SQL token cost is small at single-repo scale** and only recurs for changed files.

**Cons**

- **Staleness is the trap.** No auto-refresh; under heavy multi-dev churn the graph drifts within hours. A stale graph answers *confidently, with citations, wrongly* — worse than an honest grep miss.
- **Marginal over native assistant search.** On one repo the team knows well, the assistant's own grep/read loop already works; the graph mostly accelerates the hardest 10% of questions.
- **Process overhead.** Someone must wire `--update` into post-merge/pull hooks or CI, and the team must trust-but-verify until the habit sticks.
- **Another MCP server in context.** Ten more tool schemas in every session — small, but nonzero against the token savings.

**Recommendation:** Adopt only with automation: a post-merge hook (or CI artifact) that runs `graphify . --update` so no human ever decides to re-scan. Scope its use to architecture reviews, refactor impact checks, and onboarding — not routine edits. Without the automation, skip it; a graph nobody refreshes is a liability with a nice query interface.

---

## Factors beyond tokens

| Factor | Assessment |
|---|---|
| **Trust model** | The `EXTRACTED` / `INFERRED` tag on every edge is the tool's best feature — it tells you which answers are proof and which are guesses. Enforce a team norm: upgrade/impact decisions cite `EXTRACTED` edges or get manually verified. |
| **Privacy / compliance** | Code never leaves the machine; no account, no telemetry; query logging off by default. Clean posture for regulated estates. |
| **Supply chain** | Naming is messy: the PyPI package is `graphifyy` (double-y); the README warns other `graphify*` packages are unaffiliated, and multiple sites/repos circulate. Verify the package name character-by-character and pin the version. |
| **Operational ceiling** | JSON graph, 512 MiB cap, in-memory load. Fine per-repo; watch it at 100-repo merged scale. |
| **Windows** | Tree-sitter wheels are generally fine, but long-path limits (260-char) on deep repo trees are a known local hazard — sanity-check one large repo before batch-indexing. |

---

## Bottom line

- **Deploy it on the 100-repo estate** — the scenario the tool was designed for and where token economics are most favorable: free deterministic indexing of the Java bulk, one-time model cost for the config layer, and query-time savings on a class of question that's otherwise impractical. Pilot on 10 repos and validate inferred XML edges first.
- **Conditionally adopt on the active monorepo** — only with an automated post-merge re-scan, and scoped to architecture and impact work.
- **Keep it away from the mainframe.** No COBOL grammar means no deterministic graph; the model-read fallback is both the most expensive and least trustworthy path, on the system where wrong answers cost the most. Use mainframe-native analysis tooling there.

---

*Token-per-line figures are order-of-magnitude estimates (~12–15 tokens/line of source); language support and command details verified against the Graphify README on 2026-08-08. Graphify is Apache-2.0 open source; no pricing tier exists — all token costs flow through your own assistant/API usage.*
