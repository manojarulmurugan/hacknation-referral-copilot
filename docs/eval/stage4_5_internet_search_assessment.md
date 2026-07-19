# Stage 4.5 internet-search assessment

## Executive verdict

**Recommendation: no-go for this submission.**

The hackathon prompt does not explicitly prohibit third-party web APIs. However, a query-time feature that uses the web to correct facility facts, supply missing capabilities, relocate facilities, or change ranking would not satisfy the prompt's provenance contract for important outputs. It would also add a deployment dependency that Databricks Free Edition may not permit: current Databricks documentation says outbound internet access is restricted to a limited set of trusted domains, while custom networking and security configuration are unavailable in Free Edition.

At most, externally sourced information could be presented as a separately attributed, non-authoritative lead for human review. It should not become dataset evidence or affect readiness, verdicts, distance, candidate tiering, or rank. That narrower feature is not needed for the current demo and does not justify its reliability and provenance cost.

Because the proposal does not clear both feasibility and rubric validity, this document intentionally contains no implementation plan.

## 1. What “4.5” actually meant in the project brief

There is no pipeline **Stage 4.5** or **Phase 4.5** in `PROJECT_BRIEF_v3.md`.

The early `4.5` reference is section **4.5, “Prediction intervals” → Wilson score interval**. It describes the Wilson interval as the preferred uncertainty interval for binomial proportions, especially for small samples or proportions near zero or one. It is a statistical method for honest regional uncertainty, not internet search, scraping, geocoding, or data enrichment.

Two nearby labels are also distinct:

- **Stage 4** is deterministic capability taxonomy and tracer mapping over dataset evidence.
- **Stage 4b** was an offline cell-ranking precompute. The authoritative solo roadmap later cut it as premature optimization.

Calling the proposed feature “Stage 4.5” would therefore overwrite an existing meaning and make the architecture harder to explain. “External web enrichment” would be the accurate name if the idea were ever revisited.

## 2. Feasibility and complexity

**Complexity: high**, despite the small amount of HTTP client code involved.

### Provider and data problems

A generic search API and a geocoding API solve different problems:

- Facility-detail discovery would require a search provider such as Tavily, SerpAPI, Brave Search, or Google Places/Text Search.
- Coordinate-to-place labeling would require a reverse-geocoding provider such as Google Maps Geocoding or Mapbox.
- Search results alone do not verify that a page belongs to the same facility. The feature would also need entity resolution across chain names, branches, aliases, addresses, stale pages, directories, and copied content.
- Scraping arbitrary result pages would add per-site terms, robots policies, HTML variability, anti-bot controls, and another failure boundary. It is materially less reliable than consuming a supported API.

Tavily is a plausible search API, but it requires a separate API key and uses credits. Its current documentation lists 1,000 free credits per month, basic search at one credit, advanced search at two credits, and pay-as-you-go at $0.008 per credit. Google reverse geocoding requires a billing-enabled Cloud project and an API key or OAuth token. No such credential exists in the current `.env`.

### Authentication and deployment

The existing Stage 8 model path is structurally safer:

- `app/llm_query.py` calls the workspace's Databricks Model Serving endpoint at the configured `DATABRICKS_SERVER_HOSTNAME`.
- It authenticates with the existing Databricks bearer token.
- It is invoked only when deterministic parsing cannot fully resolve a query.
- Its output is schema-validated and then re-resolved through the internal city index.
- It cannot supply facility facts or geocode a location.
- A timeout or error falls back to the deterministic parse instead of blocking results.

A web feature would need a new third-party key provisioned as a deployed App secret or environment variable, key rotation and quota handling, and assurance that the secret never reaches logs, cards, traces, or the repository. The current Databricks credentials do not grant access to Tavily, Google, Bing, or another web provider.

### Free Edition egress is a deployment blocker

This risk is stronger than “not yet tested.” Databricks' current Free Edition limitations state:

> “Outbound internet access is restricted to a limited set of trusted domains.”

The same page states that Free Edition has:

> “No compliance enforcement, security customization, or private networking configurations.”

Databricks Apps documentation explains that third-party REST API domains normally need to be added to an egress allowlist through network policies, but also says:

> “Network policies are only available on the Enterprise tier.”

There is no evidence that `api.tavily.com`, Google Maps endpoints, SerpAPI, or arbitrary facility websites are among Free Edition's trusted domains. A local success would therefore not establish deployability, and the team cannot rely on the Enterprise allowlisting mechanism for this submission.

### Latency and reliability

The current ranker is in-process and deterministic. A live search would add DNS, TLS, provider latency, search variability, extraction latency, rate limits, quota exhaustion, and cold or denied egress to every enriched request. Searching several facilities would also create fan-out and tail-latency amplification.

To claim that the feature would “for sure work” after deployment, it would have to be verified from the actual running Free Edition App, not from a notebook or local machine:

1. Confirm DNS/TLS connectivity and successful responses to every required API domain.
2. Repeat cold and warm requests and record p50/p95 latency.
3. Exercise missing-key, denied-egress, timeout, 429, 5xx, malformed-response, and exhausted-quota cases.
4. Confirm the original shortlist renders unchanged within a strict timeout when every external call fails.
5. Repeat the deployed test after redeploy/restart and immediately before the demo.

The current platform evidence does not justify expecting step 1 to succeed.

## 3. Rubric validity

### Is it allowed?

**Not expressly forbidden, but not rubric-valid in the proposed data-correction/ranking form.**

The prompt does not say “external APIs are prohibited.” A visibly attributed external link or reviewer lead could be permissible as supplementary material. The problem is using external content as if it were part of the Evidence Engine or as an unqualified correction to the supplied dataset.

The Core Requirements say:

> “Every important output your app produces must trace back to the facility text that supports it.”

The Referral Copilot minimum workflow requires:

> “an evidence-attached shortlist” where each candidate shows “distance, matching evidence, and gaps.”

The 35% Evidence and Trust criterion asks:

> “Are outputs grounded in row-level citations?”

It also rewards honest uncertainty:

> “Since there is no ground truth, we value apps that double-check their own work.”

The research question is explicitly:

> “How do you quantify trust when there is no ground truth?”

These lines favor provenance-aware uncertainty and internal consistency checks. They do not support silently treating a search result, directory listing, or geocoder response as ground truth.

The Self-Correction stretch goal is also narrower than general web verification:

> “Implement a Validator step that cross-references extracted claims against known medical standards or internal consistency rules.”

The shipped facility-type validator, contradiction flags, provenance audit, geo-confidence discount, and internal city-cluster label already fit that language directly. A live web lookup is not required to earn the self-correction story.

### Query interpretation versus data enrichment

The existing LLM path and the proposed web path play fundamentally different roles:

- **Stage 8 LLM:** interprets user language into a bounded capability and location phrase. It uses on-platform Model Serving, validates the capability against the locked taxonomy, resolves the location through the same internal city index, and has a deterministic fallback. It does not create facility evidence.
- **Web enrichment:** would introduce new claims about a facility, its location, or its services from an off-platform source. Those claims are outside the row-level dataset provenance unless shown separately with their own URL, retrieval time, exact quotation, and uncertainty.

Therefore the Stage 8 LLM is compatible with the rubric while web-derived facility correction is not automatically compatible merely because both involve an HTTP call.

### Likely scoring effect

- **Evidence and Trust (35%): hurts** if web data changes facility facts or rank without a separate provenance model. It dilutes the project's strongest differentiator: every evidence claim has a row-level receipt.
- **Product Judgment (30%): likely hurts.** More warnings, source conflicts, loading states, and changing answers complicate a short non-technical referral workflow.
- **Technical Execution (25%): high downside.** A feature that works locally but fails in the live Free Edition App directly conflicts with “Does the app work reliably in a live demo on Free Edition?”
- **Ambition (10%): possible small upside**, but only if the feature is robust, clearly attributed, and meaningful. That upside is dominated by the other three risks and duplicates already-shipped internal validators.

## 4. Relationship to the current score and rank

The current Best-match rank is intentionally auditable:

- `evidence_component = 0.5 × verdict_base + 0.5 × readiness_score`
- `proximity_component` is a deterministic gravity decay over dataset coordinates.
- A dataset-internal geo mismatch discounts proximity confidence.
- An implausible structured facility type applies a deterministic penalty.
- The final composite blends 60% evidence and 40% proximity, while evidence-bearing and zero-evidence tiers remain separate.

Web-derived values should **not** be inserted into any of those terms:

- They should not increase `readiness_score` or promote a verdict.
- They should not count as tracers or corroborating bullets.
- They should not overwrite dataset coordinates or remove the geo discount.
- They should not alter the search band, evidence tier, or composite score.
- A lack of web results should never count as negative evidence.

Doing so would combine two incompatible evidence regimes in one number: reproducible dataset evidence and volatile external claims. The resulting score could no longer be reconstructed from the card's facility-text citations.

If external research were reconsidered after the hackathon, the defensible boundary would be a separate, explicitly labeled “external lead for manual verification” with source URL, exact quote, retrieval timestamp, match confidence, and reviewer decision. Until a human accepts that information into a governed dataset with retained provenance, it should remain outside scoring and ranking. This is a provenance boundary, not an implementation recommendation for the current submission.

## 5. Recommendation

**No-go. Do not build the live internet-search enrichment feature for the hackathon submission.**

Reasons:

1. “Stage 4.5” originally means Wilson uncertainty intervals, not web search.
2. Free Edition officially restricts outbound internet access and does not provide the Enterprise network-policy mechanism needed to allowlist arbitrary API domains.
3. No third-party search or geocoding credentials are currently provisioned.
4. Search is not ground truth; making it useful requires entity resolution, source attribution, freshness handling, and conflict policy.
5. Query-time external calls add latency and a demo-critical failure mode.
6. Using web-derived facts in scoring or ranking weakens the core row-level provenance contract.
7. The current internal geo label, geo-confidence discount, type validator, contradiction rules, and rejected-evidence display already address the motivating problems deterministically.

The stronger submission story is: the team recognized that external “verification” would create a second, less-auditable evidence regime; measured the deployment and rubric risks; and deliberately retained an internal, reproducible trust model that admits uncertainty instead of pretending the web is ground truth.

## Sources reviewed

- `PROJECT_BRIEF_v3.md`, especially §§4.5, 5, 6, 9, 14.5, and 15.
- `Hackathon Prompt.pdf`, especially §§2, 3, 4, 5, and 6.
- `app/llm_query.py`.
- Databricks, “Free Edition limitations”: <https://docs.databricks.com/aws/en/getting-started/free-edition-limitations>
- Databricks, “Configure networking for Databricks Apps”: <https://docs.databricks.com/aws/en/dev-tools/databricks-apps/networking>
- Tavily, “Credits & Pricing”: <https://docs.tavily.com/documentation/api-credits>
- Google Maps Platform, “Geocoding API Usage and Billing”: <https://developers.google.com/maps/documentation/geocoding/usage-and-billing>
