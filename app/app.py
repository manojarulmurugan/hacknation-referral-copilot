"""Referral Copilot Dash application.

Run: PYTHONPATH=. python -m app.app, then open http://127.0.0.1:8050
(`python app/app.py` directly will NOT work -- it puts app/ itself on
sys.path[0], which shadows the `app` package because app/app.py collides
with the package name; `-m app.app` from the repo root avoids that.)

The deployed app uses the repo-root gunicorn command in app.yaml.
"""

from __future__ import annotations

import dash
import dash_bootstrap_components as dbc
import pandas as pd
from dash import ALL, Dash, Input, Output, State, dcc, html, no_update

from app.cards import build_card_data
from app.fallback import search_raw_evidence
from app.persistence import load_shortlist_repository
from app.query import parse_query, parse_query_with_fallback
from app.ranking import rank_candidates
from app.store import load_store
from pipeline.stage4_taxonomy_mapping import LOCKED_CAPABILITY_IDS

STORE = load_store()
SHORTLIST = load_shortlist_repository()
CAPABILITY_DISPLAY_NAMES = STORE.taxonomy.get("display_names", {})


def capability_name(capability_id: str) -> str:
    return CAPABILITY_DISPLAY_NAMES.get(
        capability_id, capability_id.replace("_", " ").title()
    )

EXAMPLE_QUERIES = [
    "dialysis near Jaipur",
    "emergency surgery near Patna",
    "ICU near Nashik",
    "maternity near Lucknow",
    # Deliberately phrased with no vocabulary-matching capability keyword
    # (verified: the deterministic parser alone returns capability_id=None
    # for this exact string) -- showcases the Stage 8 LLM fallback
    # (app/llm_query.py) live, resolving "kidneys filtered regularly" -> dialysis.
    "my father needs his kidneys filtered regularly, somewhere near Bhopal",
]

app = Dash(__name__, external_stylesheets=[dbc.themes.BOOTSTRAP])
app.title = "Referral Copilot"
server = app.server


# ---------------------------------------------------------------- layout --

app.layout = dbc.Container(
    [
        dcc.Store(id="last-query-context", storage_type="memory"),
        html.Div(
            [
                html.H1("Referral Copilot"),
                html.P(
                    "Evidence-attached facility search for care coordinators.",
                    className="subtitle",
                ),
            ],
            className="app-header",
        ),
        html.Div(
            [
                html.Label("Your name", htmlFor="user-name", className="identity-label"),
                dcc.Input(
                    id="user-name",
                    type="text",
                    placeholder="e.g. Asha",
                    persistence=True,
                    persistence_type="local",
                    className="identity-input",
                ),
                html.Small(
                    "Used only to keep your saved referrals separate.",
                    className="identity-hint",
                ),
            ],
            className="identity-row",
        ),
        dbc.Row(
            [
                dbc.Col(
                    dcc.Input(
                        id="query-input",
                        type="text",
                        placeholder='e.g. "dialysis near Jaipur"',
                        className="query-input",
                    ),
                    width=9,
                ),
                dbc.Col(dbc.Button("Search", id="search-btn"), width=3, className="search-btn-col"),
            ],
            className="search-row",
        ),
        html.Div(
            [
                dbc.Button(q, id=f"chip-{i}", n_clicks=0, size="sm", outline=True, className="example-chip")
                for i, q in enumerate(EXAMPLE_QUERIES)
            ],
            className="chips-row",
        ),
        dbc.RadioItems(
            id="sort-mode",
            options=[
                {"label": "Best match", "value": "best"},
                {"label": "Nearest", "value": "nearest"},
                {"label": "Most evidence", "value": "most_evidence"},
            ],
            value="best",
            inline=True,
            className="sort-toggle",
        ),
        html.Div(id="save-feedback", className="save-feedback"),
        html.Div(id="results-area", className="results-area"),
        html.Div(
            [
                html.H2("My shortlist"),
                html.P(
                    f"Saved referrals · {SHORTLIST.backend_name}",
                    className="shortlist-backend",
                ),
                html.Div(id="shortlist-area"),
            ],
            className="shortlist-panel",
        ),
    ],
    fluid=True,
    className="app-container",
)


# ------------------------------------------------------------- rendering --

def render_initial_state():
    return html.P("Enter a location and care need above, or try an example.", className="empty-state")


def render_capability_error(query_text: str):
    valid = ", ".join(
        capability_name(capability_id)
        for capability_id in sorted(LOCKED_CAPABILITY_IDS)
    )
    return html.Div(
        [
            html.P(f'Could not identify a care need in "{query_text}".', className="empty-state empty-state-error"),
            html.P(f"Recognized care needs: {valid}", className="empty-state-hint"),
        ]
    )


def render_city_error(query_text: str):
    return html.Div(
        [
            html.P(f'Could not identify a location in "{query_text}".', className="empty-state empty-state-error"),
            html.P('Try including a city name, e.g. "dialysis near Jaipur".', className="empty-state-hint"),
        ]
    )


def render_no_results(parsed):
    return html.P(
        f"No facilities with usable location data found for "
        f"{capability_name(parsed.capability_id)} near {parsed.city_label}.",
        className="empty-state",
    )


def render_raw_evidence_card(row: pd.Series):
    return html.Div(
        [
            html.Div(
                [
                    html.Span(row.get("name") or "Unnamed facility", className="facility-name"),
                    html.Span(
                        f"{row.get('address_city') or '?'}, "
                        f"{row.get('address_stateOrRegion') or '?'}",
                        className="facility-location",
                    ),
                    html.Span(
                        f"{row['distance_km']:.1f} km away",
                        className="facility-distance",
                    ),
                ],
                className="card-header",
            ),
            html.Div("□ Exploratory evidence match", className="verdict-line verdict-claimed_only"),
            html.P(
                "This care need is outside the scored 20-capability taxonomy. "
                "These are literal text matches, not readiness assessments.",
                className="gaps-line",
            ),
            html.P(
                f"{row['raw_evidence_count']} matching evidence bullet(s).",
                className="source-field",
            ),
            html.Ul(
                [
                    html.Li(
                        [
                            html.Q(item["text"]),
                            html.Small(
                                f" ({item['source_field']})",
                                className="source-field",
                            ),
                        ]
                    )
                    for item in row["raw_evidence_items"]
                ]
            ),
        ],
        className="facility-card",
    )


def render_raw_evidence_results(parsed, ranked: pd.DataFrame, terms: list[str]):
    need = " ".join(terms)
    children = [
        html.P(
            f"Exploratory results for “{need}” near {parsed.city_label}",
            className="results-summary",
        ),
        html.P(
            "This need is outside our scored taxonomy. Results below contain "
            "the literal search terms in their source evidence; no readiness "
            "verdict is inferred.",
            className="llm-note",
        ),
    ]
    if ranked.empty:
        children.append(
            html.P(
                "No matching raw evidence was found. Try a more specific medical term.",
                className="empty-state",
            )
        )
        return html.Div(children)
    if bool(ranked["search_widened"].iloc[0]):
        band = ranked["search_band_km"].iloc[0]
        band_text = f"{band:.0f} km" if pd.notna(band) else "nationwide"
        children.append(
            html.P(
                f"Few literal matches were nearby, so the search widened to {band_text}.",
                className="tier-separator",
            )
        )
    children.extend(
        render_raw_evidence_card(row) for _, row in ranked.iterrows()
    )
    return html.Div(children)


def render_domain_bar(domain: str, score: float):
    pct = round(score * 100)
    return html.Div(
        [
            html.Span(domain, className="domain-label"),
            html.Div(html.I(style={"width": f"{pct}%"}), className="domain-bar"),
            html.Span(f"{pct}%", className="domain-pct"),
        ],
        className="domain-row",
    )


def render_evidence_item(item: dict):
    # Full source sentence with the matched substring highlighted, not just
    # the short matched substring alone (second-opinion review catch:
    # supporting_quote alone averages ~13 chars, e.g. "ICU" -- not a real
    # row-level citation a planner could actually read and judge).
    if item["highlight_match"]:
        text_children = [
            item["highlight_before"],
            html.Mark(item["highlight_match"]),
            item["highlight_after"],
        ]
    else:
        text_children = [item["full_text"]]
    return html.Li(
        [
            html.Span(
                item["tracer_id"].replace("_", " ").capitalize(),
                className="tracer-tag",
            ),
            html.Q(text_children),
            html.Small(f" ({item['source_field']})", className="source-field"),
        ]
    )


def render_card(data: dict):
    children = []

    if data["contradiction_flags"]:
        children.append(
            html.Div(
                [html.Div("⚠ " + msg) for msg in data["contradiction_flags"]],
                className="contradiction-banner",
            )
        )

    if data["type_implausible"]:
        children.append(
            html.P(
                f"⚠ Facility type on record is '{data['facility_type']}', which is inconsistent with a "
                f"{data['capability_id'].replace('_', ' ')} capability — this evidence is likely mis-tagged. "
                "Verify before referring.",
                className="geo-mismatch-note",
            )
        )

    distance = data["distance_km"]
    children.append(
        html.Div(
            [
                html.Span(data["name"], className="facility-name"),
                html.Span(f"{data['city'] or '?'}, {data['state'] or '?'}", className="facility-location"),
                html.Span(f"{distance:.1f} km away" if distance is not None else "", className="facility-distance"),
            ],
            className="card-header",
        )
    )

    if data["geo_mismatch"]:
        # Confirmed real case: a facility's own address_city can disagree
        # with its own recorded coordinates by hundreds of km (source-data
        # contamination, not a ranking bug -- the distance above is
        # computed correctly from whatever coordinates this facility has on
        # file). Surfaced plainly rather than silently trusting either
        # field, consistent with Stage 3's whole contamination-audit stance.
        children.append(
            html.P(
                f"⚠ Coordinates place this facility near {data['coordinate_city'] or 'a different city'}, "
                f"but the address on file says {data['city']} (likely a headquarters listing). "
                "Distance shown is from the coordinates.",
                className="geo-mismatch-note",
            )
        )

    children.append(
        html.Div(
            f"{data['verdict_glyph']} {data['verdict_label']}",
            className=f"verdict-line verdict-{data['verdict']}",
        )
    )

    children.append(
        html.Div(
            [render_domain_bar(d, s) for d, s in data["domain_scores"]],
            className="domain-bars",
        )
    )

    if data["gaps"]:
        children.append(html.P(f"Not evidenced: {', '.join(data['gaps'])}", className="gaps-line"))

    for domain in sorted(data["evidence_by_domain"]):
        items = data["evidence_by_domain"][domain]
        children.append(
            html.Div(
                [html.Strong(domain), html.Ul([render_evidence_item(it) for it in items])],
                className="evidence-domain",
            )
        )

    if data["rejected_evidence"]:
        children.append(
            html.Details(
                [
                    html.Summary(f"Evidence rejected by provenance audit ({len(data['rejected_evidence'])})"),
                    html.Ul([render_evidence_item(it) for it in data["rejected_evidence"]]),
                ],
                className="rejected-details",
            )
        )

    children.append(
        html.Details(
            [
                html.Summary("How this was scored"),
                html.P(
                    f"Readiness score {data['readiness_score']:.2f} (informational, SARA-style, mean of domain "
                    f"scores). {data['distinct_tracer_count']} distinct tracer item(s) matched from "
                    f"{data['distinct_bullet_count']} distinct evidence bullet(s) across "
                    f"{data['distinct_source_field_count']} source field(s). Facility has "
                    f"{data['completeness_bullet_count']} total evidence bullets on record. "
                    f"‘Corroborated’ requires ≥ 2 distinct tracers across ≥ 2 source fields."
                ),
                html.P(
                    f"Best-match score {data['composite_score']:.3f}: evidence "
                    f"{data['evidence_component']:.3f}, proximity {data['proximity_component']:.3f}"
                    f"{' (location-confidence discounted)' if data['geo_discounted'] else ''}"
                    f"{' (facility-type penalty applied)' if data['type_implausible'] else ''}. "
                    "Best match blends 60% evidence and 40% proximity; Nearest and Most evidence remain pure-axis views."
                ) if data["composite_score"] is not None else None,
            ],
            className="scoring-details",
        )
    )

    children.append(
        dbc.Button(
            "Save to shortlist",
            id={
                "type": "save-shortlist",
                "facility_id": data["facility_id"],
                "capability_id": data["capability_id"],
                "verdict": data["verdict"],
                "distance_km": round(float(data["distance_km"]), 3),
            },
            n_clicks=0,
            size="sm",
            className="save-btn",
        )
    )

    return html.Div(children, className="facility-card")


def render_results(query_text: str, sort_mode: str = "best"):
    # A plainly stated out-of-taxonomy need with a resolved city should not
    # wait on Model Serving before trying the transparent literal-evidence
    # fallback. If there are no raw matches, continue to the LLM so unfamiliar
    # paraphrases of a scored capability can still be resolved.
    deterministic = parse_query(query_text, STORE)
    if deterministic.capability_id is None and deterministic.city_label is not None:
        raw_ranked, terms = search_raw_evidence(
            STORE.evidence_bullets,
            STORE.facilities,
            query_text,
            deterministic.city_label,
            deterministic.origin_lat,
            deterministic.origin_lon,
            sort_mode=sort_mode,
        )
        if not raw_ranked.empty:
            return render_raw_evidence_results(deterministic, raw_ranked, terms)

    parsed = parse_query_with_fallback(query_text, STORE)
    if parsed.capability_id is None:
        if parsed.city_label is None:
            return render_capability_error(query_text)
        raw_ranked, terms = search_raw_evidence(
            STORE.evidence_bullets,
            STORE.facilities,
            query_text,
            parsed.city_label,
            parsed.origin_lat,
            parsed.origin_lon,
            sort_mode=sort_mode,
        )
        if terms:
            return render_raw_evidence_results(parsed, raw_ranked, terms)
        return render_capability_error(query_text)
    if parsed.city_label is None:
        return render_city_error(query_text)

    ranked = rank_candidates(
        STORE.readiness, STORE.facilities, parsed.capability_id, parsed.origin_lat, parsed.origin_lon, sort_mode
    )
    if ranked.empty:
        return render_no_results(parsed)

    summary = html.P(
        f"{len(ranked)} facilities for “{capability_name(parsed.capability_id)}” "
        f"near {parsed.city_label} "
        f"(matched against {parsed.city_facility_count} facilities on record there).",
        className="results-summary",
    )

    children = [summary]

    search_widened = bool(ranked["search_widened"].iloc[0])
    if search_widened:
        # Confirmed live bug this fixes: "most evidence" mode with no
        # distance cap could surface a facility 1,558km away as the top
        # result. Ranking now widens through fixed bands (50/150/300/600km)
        # only as far as needed to find MIN_EVIDENCE_RESULTS evidenced
        # facilities -- shown here so a wide search is never silently
        # passed off as local.
        band_km = ranked["search_band_km"].iloc[0]
        band_text = f"{band_km:.0f} km" if pd.notna(band_km) else "nationwide"
        children.append(
            html.P(
                f"Few well-evidenced options near {parsed.city_label} -- search widened to {band_text} "
                "to find enough candidates.",
                className="tier-separator",
            )
        )

    if parsed.used_llm_fallback:
        # Stage 8: the deterministic parser couldn't fully resolve this
        # query, so a live Databricks Model Serving call filled in what was
        # missing. Shown plainly, not hidden -- editable/manual correction
        # is the brief's own "never blocks the user" recommendation; a full
        # edit form is out of scope for this time-box, so the note itself
        # (plus the always-visible search box to just retype) stands in.
        children.append(
            html.P(
                f"Interpreted with AI assistance: "
                f"“{capability_name(parsed.capability_id)}” near {parsed.city_label}. "
                "Not quite right? Just search again with different words.",
                className="llm-note",
            )
        )

    # Evidence-bearing facilities always come first (app/ranking.py never
    # lets pure distance bury them); zero-evidence facilities only appear as
    # a clearly separated fallback -- never silently mixed in as if they
    # were equally supported.
    shown_fallback_heading = False
    for _, row in ranked.iterrows():
        if not row["is_evidence_tier"] and not shown_fallback_heading:
            children.append(
                html.P(
                    "No recorded evidence for this capability at the facilities below "
                    "-- shown because closer evidenced options ran out.",
                    className="tier-separator",
                )
            )
            shown_fallback_heading = True
        card_data = build_card_data(row, STORE.matches, STORE.taxonomy, STORE.bullet_text_by_id)
        children.append(render_card(card_data))

    return html.Div(children)


def render_shortlist(user_name: str):
    if not user_name or not user_name.strip():
        return html.P("Enter your name to view saved referrals.", className="empty-state-hint")
    items = SHORTLIST.list_for_user(user_name)
    if not items:
        return html.P("No saved referrals yet.", className="empty-state-hint")

    facilities = STORE.facilities.set_index("unique_id")
    children = []
    for item in items:
        facility = facilities.loc[item.facility_id] if item.facility_id in facilities.index else None
        name = facility.get("name") if facility is not None else item.facility_id
        city = facility.get("address_city") if facility is not None else None
        query_text = item.query_context.get("query_text", "")
        children.append(
            html.Div(
                [
                    html.Strong(name or "Unnamed facility", className="facility-name"),
                    html.Span(
                        f"{capability_name(item.capability_id)} · {item.verdict.replace('_', ' ')}",
                        className="shortlist-meta",
                    ),
                    html.Span(
                        f"{item.distance_km:.1f} km · {city or 'location unavailable'}",
                        className="shortlist-meta",
                    ),
                    html.Small(f'Saved from “{query_text}”', className="source-field"),
                ],
                className="shortlist-item",
            )
        )
    return html.Div(children)


# -------------------------------------------------------------- callback --

@app.callback(
    Output("results-area", "children"),
    Output("query-input", "value"),
    Output("last-query-context", "data"),
    Input("search-btn", "n_clicks"),
    Input("query-input", "n_submit"),
    Input("sort-mode", "value"),
    Input("chip-0", "n_clicks"),
    Input("chip-1", "n_clicks"),
    Input("chip-2", "n_clicks"),
    Input("chip-3", "n_clicks"),
    Input("chip-4", "n_clicks"),
    State("query-input", "value"),
)
def on_search(_n_clicks, _n_submit, sort_mode, _c0, _c1, _c2, _c3, _c4, current_text):
    triggered = dash.ctx.triggered_id
    if isinstance(triggered, str) and triggered.startswith("chip-"):
        query_text = EXAMPLE_QUERIES[int(triggered.split("-")[1])]
    else:
        query_text = current_text

    if not query_text or not query_text.strip():
        return render_initial_state(), no_update, no_update

    mode = sort_mode or "best"
    return (
        render_results(query_text, mode),
        query_text,
        {"query_text": query_text, "sort_mode": mode},
    )


@app.callback(
    Output("shortlist-area", "children"),
    Output("save-feedback", "children"),
    Input("user-name", "value"),
    Input({"type": "save-shortlist", "facility_id": ALL, "capability_id": ALL,
           "verdict": ALL, "distance_km": ALL}, "n_clicks"),
    State("last-query-context", "data"),
)
def update_shortlist(user_name, _save_clicks, query_context):
    triggered = dash.ctx.triggered_id
    feedback = ""
    if isinstance(triggered, dict) and triggered.get("type") == "save-shortlist":
        if not any(int(clicks or 0) > 0 for clicks in (_save_clicks or [])):
            return render_shortlist(user_name or ""), ""
        if not user_name or not user_name.strip():
            return render_shortlist(""), "Enter your name before saving."
        if not query_context:
            return render_shortlist(user_name), "Run a search before saving."
        try:
            created = SHORTLIST.save(
                user_name=user_name,
                facility_id=triggered["facility_id"],
                capability_id=triggered["capability_id"],
                query_context=query_context,
                verdict=triggered["verdict"],
                distance_km=triggered["distance_km"],
            )
            feedback = "Saved to your shortlist." if created else "Already in your shortlist."
        except Exception:
            feedback = "Could not save right now; your search results are unchanged."
    try:
        return render_shortlist(user_name or ""), feedback
    except Exception:
        return html.P("Shortlist is temporarily unavailable.", className="empty-state-error"), feedback


if __name__ == "__main__":
    app.run(debug=True)
