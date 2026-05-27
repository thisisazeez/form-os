"""
In-repo Survey Template Catalog (WS-14 — the "Create a new survey" gallery).

A VERSIONED, in-code set of ready-made survey definitions (NOT a DB model — so they ship
with the code, diff in review, and never need a migration). The gallery view
(``apps.surveys.views_htmx.survey_new``) reads ``CATALOG`` to render template cards with
channel/industry/role filter chips + search; picking one POSTs its ``slug`` and the survey
is created from a DEEP COPY of its ``definition`` via ``services.create_survey``.

Each entry is a dict::

    {
      "slug":        URL-safe stable id (the POST key + gallery anchor),
      "name":        survey name (becomes Survey.name),
      "description": one-line gallery-card blurb,
      "channel":     one of CHANNELS keys (link / website / app),
      "industry":    one of INDUSTRIES keys (ecommerce / saas / other),
      "role":        one of ROLES keys (pm / cs / marketing / sales / people),
      "definition":  a FULL survey definition (name/questions/languages/styling/...),
                     guaranteed to pass validate_survey_definition (a test loops over the
                     whole catalog and asserts this — the guard against shipping broken ones).
    }

The filter vocab maps a machine key → human label; the gallery renders the labels and filters
on the keys (simple GET query params, server-rendered like the existing status chips). "All"
is the implicit no-filter default and is not stored on entries.
"""
from __future__ import annotations

import copy
from typing import Any

from ._builders import (
    EN_LANGUAGES,
    choice,
    end_screen,
    end_survey,
    jump_to,
    q_consent,
    q_contact_info,
    q_cta,
    q_date,
    q_multi_select,
    q_nps,
    q_number,
    q_open_text,
    q_ranking,
    q_rating,
    q_single_select,
    rule,
)

# --------------------------------------------------------------------------- #
#  Filter vocabularies (key -> human label). The gallery renders these as chip #
#  rows; "all" is the implicit default (no entry carries it).                  #
#  Labels match FEATURE_PARITY.md WS-14 so a future screenshot diff lines up.  #
# --------------------------------------------------------------------------- #
CHANNELS: dict[str, str] = {
    "link": "Link Survey",
    "website": "Website Survey",
    "app": "App Survey",
}
INDUSTRIES: dict[str, str] = {
    "ecommerce": "E-Commerce",
    "saas": "SaaS",
    "other": "Other",
}
ROLES: dict[str, str] = {
    "pm": "Product Manager",
    "cs": "Customer Success",
    "marketing": "Marketing",
    "sales": "Sales",
    "people": "People Manager",
}


def _definition(name: str, questions: list[dict[str, Any]], *,
                endings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Assemble a full, schema-valid survey definition (English, no theming)."""
    return {
        "name": name,
        "questions": questions,
        "languages": copy.deepcopy(EN_LANGUAGES),
        "styling": {},
        "endings": endings if endings is not None else [end_screen("end", "Thank you!")],
    }


# --------------------------------------------------------------------------- #
#  The catalog. ~20 Formbricks-style templates across channels/industries/roles. #
# --------------------------------------------------------------------------- #
CATALOG: list[dict[str, Any]] = [
    # ---- NPS ---------------------------------------------------------------
    {
        "slug": "nps",
        "name": "Net Promoter Score (NPS)",
        "description": "Measure customer loyalty with the classic 0–10 recommend question and a follow-up reason.",
        "channel": "link",
        "industry": "saas",
        "role": "pm",
        "definition": _definition(
            "Net Promoter Score (NPS)",
            [
                q_nps(
                    "nps_score",
                    "How likely are you to recommend us to a friend or colleague?",
                    required=True,
                    lower="Not at all likely",
                    upper="Extremely likely",
                ),
                q_open_text(
                    "nps_reason",
                    "What is the main reason for your score?",
                    long=True,
                    placeholder="Tell us a bit more…",
                ),
            ],
        ),
    },
    # ---- CSAT --------------------------------------------------------------
    {
        "slug": "csat",
        "name": "Customer Satisfaction (CSAT)",
        "description": "A quick satisfaction rating plus an open comment to capture the why behind the score.",
        "channel": "link",
        "industry": "saas",
        "role": "cs",
        "definition": _definition(
            "Customer Satisfaction (CSAT)",
            [
                q_rating(
                    "csat_rating",
                    "How satisfied are you with our product?",
                    range_=5,
                    scale="smiley",
                    required=True,
                    lower="Very unsatisfied",
                    upper="Very satisfied",
                ),
                q_open_text(
                    "csat_comment",
                    "What's the primary reason for your rating?",
                    long=True,
                ),
            ],
        ),
    },
    # ---- CES ---------------------------------------------------------------
    {
        "slug": "ces",
        "name": "Customer Effort Score (CES)",
        "description": "Find out how easy it was for customers to get what they needed.",
        "channel": "link",
        "industry": "saas",
        "role": "cs",
        "definition": _definition(
            "Customer Effort Score (CES)",
            [
                q_rating(
                    "ces_effort",
                    "How easy was it to handle your request?",
                    range_=7,
                    scale="number",
                    required=True,
                    lower="Very difficult",
                    upper="Very easy",
                ),
                q_open_text("ces_why", "What made it easy or hard?", long=True),
            ],
        ),
    },
    # ---- PMF (Superhuman) --------------------------------------------------
    {
        "slug": "pmf",
        "name": "Product-Market Fit (Superhuman)",
        "description": "The Superhuman PMF survey — gauge how disappointed users would be without your product.",
        "channel": "link",
        "industry": "saas",
        "role": "pm",
        "definition": _definition(
            "Product-Market Fit (Superhuman)",
            [
                q_single_select(
                    "pmf_disappointment",
                    "How would you feel if you could no longer use our product?",
                    [
                        choice("very", "Very disappointed"),
                        choice("somewhat", "Somewhat disappointed"),
                        choice("not", "Not disappointed"),
                    ],
                    required=True,
                ),
                q_open_text(
                    "pmf_benefit",
                    "What is the main benefit you receive from our product?",
                    long=True,
                ),
                q_open_text(
                    "pmf_improve",
                    "How can we improve our product for you?",
                    long=True,
                ),
            ],
        ),
    },
    # ---- Cart abandonment --------------------------------------------------
    {
        "slug": "cart-abandonment",
        "name": "Cart Abandonment",
        "description": "Understand why shoppers leave items behind so you can recover lost sales.",
        "channel": "website",
        "industry": "ecommerce",
        "role": "marketing",
        "definition": _definition(
            "Cart Abandonment",
            [
                q_single_select(
                    "cart_reason",
                    "What stopped you from completing your purchase today?",
                    [
                        choice("shipping", "Shipping cost was too high"),
                        choice("price", "Item was too expensive"),
                        choice("payment", "Payment options didn't work for me"),
                        choice("comparing", "I was just comparing prices"),
                        choice("other", "Other", is_other=True),
                    ],
                    required=True,
                ),
                q_open_text(
                    "cart_change",
                    "What would have convinced you to check out?",
                    long=True,
                ),
            ],
        ),
    },
    # ---- Site abandonment --------------------------------------------------
    {
        "slug": "site-abandonment",
        "name": "Site Abandonment",
        "description": "Catch visitors as they leave to learn what they were looking for and didn't find.",
        "channel": "website",
        "industry": "ecommerce",
        "role": "marketing",
        "definition": _definition(
            "Site Abandonment",
            [
                q_single_select(
                    "site_goal",
                    "What were you hoping to do on our site today?",
                    [
                        choice("browse", "Just browsing"),
                        choice("buy", "Make a purchase"),
                        choice("research", "Research a product"),
                        choice("support", "Find support or help"),
                        choice("other", "Something else", is_other=True),
                    ],
                    required=True,
                ),
                q_open_text(
                    "site_missing",
                    "Was there anything you couldn't find?",
                    long=True,
                ),
            ],
        ),
    },
    # ---- Onboarding feedback ----------------------------------------------
    {
        "slug": "onboarding-feedback",
        "name": "Onboarding Feedback",
        "description": "See how new users experience your setup flow and where they get stuck.",
        "channel": "app",
        "industry": "saas",
        "role": "pm",
        "definition": _definition(
            "Onboarding Feedback",
            [
                q_rating(
                    "onb_ease",
                    "How easy was it to get started?",
                    range_=5,
                    scale="star",
                    required=True,
                    lower="Very hard",
                    upper="Very easy",
                ),
                q_single_select(
                    "onb_stuck",
                    "Did you get stuck anywhere during setup?",
                    [
                        choice("no", "No, it was smooth"),
                        choice("yes", "Yes, at some point"),
                    ],
                    required=True,
                    logic=[
                        rule(
                            "onb_skip_detail",
                            left="onb_stuck",
                            operator="equals",
                            value="no",
                            action=jump_to("onb_value"),
                        )
                    ],
                ),
                q_open_text(
                    "onb_detail",
                    "Where did you get stuck, and what happened?",
                    long=True,
                ),
                q_open_text(
                    "onb_value",
                    "What's the one thing you're hoping to achieve with us?",
                    long=True,
                ),
            ],
        ),
    },
    # ---- Churn / cancellation flow ----------------------------------------
    {
        "slug": "churn",
        "name": "Churn / Cancellation",
        "description": "Capture the reason behind a cancellation and what could win the customer back.",
        "channel": "app",
        "industry": "saas",
        "role": "cs",
        "definition": _definition(
            "Churn / Cancellation",
            [
                q_single_select(
                    "churn_reason",
                    "What's the main reason you're cancelling?",
                    [
                        choice("expensive", "Too expensive"),
                        choice("missing", "Missing features I need"),
                        choice("hard", "Too hard to use"),
                        choice("switching", "Switching to another tool"),
                        choice("nolonger", "No longer need it"),
                        choice("other", "Other", is_other=True),
                    ],
                    required=True,
                ),
                q_open_text(
                    "churn_winback",
                    "Is there anything we could have done to keep you?",
                    long=True,
                ),
            ],
        ),
    },
    # ---- Feature request ---------------------------------------------------
    {
        "slug": "feature-request",
        "name": "Feature Request",
        "description": "Let customers tell you what to build next and how important it is to them.",
        "channel": "app",
        "industry": "saas",
        "role": "pm",
        "definition": _definition(
            "Feature Request",
            [
                q_open_text(
                    "fr_what",
                    "What feature would you most like to see?",
                    long=True,
                    required=True,
                ),
                q_open_text(
                    "fr_why",
                    "How would this feature help you?",
                    long=True,
                ),
                q_rating(
                    "fr_importance",
                    "How important is this to you?",
                    range_=5,
                    scale="number",
                    lower="Nice to have",
                    upper="Critical",
                ),
            ],
        ),
    },
    # ---- eNPS --------------------------------------------------------------
    {
        "slug": "enps",
        "name": "Employee NPS (eNPS)",
        "description": "Measure how likely your employees are to recommend your company as a place to work.",
        "channel": "link",
        "industry": "other",
        "role": "people",
        "definition": _definition(
            "Employee NPS (eNPS)",
            [
                q_nps(
                    "enps_score",
                    "How likely are you to recommend this company as a place to work?",
                    required=True,
                    lower="Not at all likely",
                    upper="Extremely likely",
                ),
                q_open_text(
                    "enps_reason",
                    "What's the main reason for your score?",
                    long=True,
                ),
            ],
        ),
    },
    # ---- Product feedback --------------------------------------------------
    {
        "slug": "product-feedback",
        "name": "Product Feedback",
        "description": "A balanced read on what users love and what frustrates them about your product.",
        "channel": "app",
        "industry": "saas",
        "role": "pm",
        "definition": _definition(
            "Product Feedback",
            [
                q_rating(
                    "pf_overall",
                    "Overall, how would you rate the product?",
                    range_=5,
                    scale="star",
                    required=True,
                ),
                q_open_text("pf_love", "What do you love most about it?", long=True),
                q_open_text("pf_frustrate", "What frustrates you the most?", long=True),
            ],
        ),
    },
    # ---- Lead qualification ------------------------------------------------
    {
        "slug": "lead-qualification",
        "name": "Lead Qualification",
        "description": "Qualify inbound leads by company size, role, and timeline before routing to sales.",
        "channel": "link",
        "industry": "saas",
        "role": "sales",
        "definition": _definition(
            "Lead Qualification",
            [
                q_single_select(
                    "lq_size",
                    "How many people work at your company?",
                    [
                        choice("solo", "Just me"),
                        choice("small", "2–50"),
                        choice("mid", "51–500"),
                        choice("large", "500+"),
                    ],
                    required=True,
                ),
                q_single_select(
                    "lq_role",
                    "What best describes your role?",
                    [
                        choice("exec", "Executive / Founder"),
                        choice("manager", "Manager"),
                        choice("ic", "Individual contributor"),
                        choice("other", "Other", is_other=True),
                    ],
                    required=True,
                ),
                q_single_select(
                    "lq_timeline",
                    "When are you looking to get started?",
                    [
                        choice("now", "Right away"),
                        choice("quarter", "This quarter"),
                        choice("later", "Just exploring"),
                    ],
                    required=True,
                ),
                q_contact_info(
                    "lq_contact",
                    "Where can our team reach you?",
                    fields={
                        "firstName": {"show": True, "required": True},
                        "email": {"show": True, "required": True},
                        "company": {"show": True, "required": False},
                    },
                ),
            ],
        ),
    },
    # ---- Event feedback ----------------------------------------------------
    {
        "slug": "event-feedback",
        "name": "Event Feedback",
        "description": "Gather attendee ratings and takeaways right after an event or webinar.",
        "channel": "link",
        "industry": "other",
        "role": "marketing",
        "definition": _definition(
            "Event Feedback",
            [
                q_rating(
                    "ev_overall",
                    "How would you rate the event overall?",
                    range_=5,
                    scale="star",
                    required=True,
                ),
                q_single_select(
                    "ev_recommend",
                    "Would you attend another event from us?",
                    [
                        choice("yes", "Definitely"),
                        choice("maybe", "Maybe"),
                        choice("no", "Probably not"),
                    ],
                    required=True,
                ),
                q_open_text("ev_best", "What was the best part?", long=True),
                q_open_text("ev_improve", "What could we improve next time?", long=True),
            ],
        ),
    },
    # ---- Course evaluation -------------------------------------------------
    {
        "slug": "course-evaluation",
        "name": "Course Evaluation",
        "description": "Collect structured feedback on course content, pacing, and the instructor.",
        "channel": "link",
        "industry": "other",
        "role": "people",
        "definition": _definition(
            "Course Evaluation",
            [
                q_rating(
                    "ce_content",
                    "How would you rate the course content?",
                    range_=5,
                    scale="star",
                    required=True,
                ),
                q_rating(
                    "ce_instructor",
                    "How would you rate the instructor?",
                    range_=5,
                    scale="star",
                    required=True,
                ),
                q_single_select(
                    "ce_pace",
                    "How was the pace of the course?",
                    [
                        choice("slow", "Too slow"),
                        choice("right", "Just right"),
                        choice("fast", "Too fast"),
                    ],
                    required=True,
                ),
                q_open_text("ce_comments", "Any additional comments?", long=True),
            ],
        ),
    },
    # ---- Website feedback --------------------------------------------------
    {
        "slug": "website-feedback",
        "name": "Website Feedback",
        "description": "A short intercept survey to learn how visitors rate and use your website.",
        "channel": "website",
        "industry": "other",
        "role": "marketing",
        "definition": _definition(
            "Website Feedback",
            [
                q_rating(
                    "wf_rating",
                    "How would you rate your experience on our website?",
                    range_=5,
                    scale="smiley",
                    required=True,
                ),
                q_single_select(
                    "wf_found",
                    "Did you find what you were looking for?",
                    [
                        choice("yes", "Yes"),
                        choice("partly", "Partly"),
                        choice("no", "No"),
                    ],
                    required=True,
                ),
                q_open_text("wf_improve", "How can we make the site better?", long=True),
            ],
        ),
    },
    # ---- App feedback ------------------------------------------------------
    {
        "slug": "app-feedback",
        "name": "App Feedback",
        "description": "In-app pulse on satisfaction and the features users want most.",
        "channel": "app",
        "industry": "saas",
        "role": "pm",
        "definition": _definition(
            "App Feedback",
            [
                q_rating(
                    "af_satisfaction",
                    "How satisfied are you with the app?",
                    range_=5,
                    scale="smiley",
                    required=True,
                ),
                q_multi_select(
                    "af_areas",
                    "Which areas would you most like us to improve?",
                    [
                        choice("speed", "Speed & performance"),
                        choice("design", "Design & usability"),
                        choice("features", "More features"),
                        choice("reliability", "Reliability & bugs"),
                        choice("other", "Other", is_other=True),
                    ],
                ),
                q_open_text("af_comment", "Anything else you'd like to share?", long=True),
            ],
        ),
    },
    # ---- Contact form ------------------------------------------------------
    {
        "slug": "contact-form",
        "name": "Contact Form",
        "description": "A simple way for prospects and customers to reach out with a message.",
        "channel": "website",
        "industry": "other",
        "role": "sales",
        "definition": _definition(
            "Contact Form",
            [
                q_contact_info(
                    "cf_contact",
                    "Let's get in touch",
                    fields={
                        "firstName": {"show": True, "required": True},
                        "lastName": {"show": True, "required": False},
                        "email": {"show": True, "required": True},
                        "company": {"show": True, "required": False},
                    },
                    required=True,
                ),
                q_open_text(
                    "cf_message",
                    "How can we help you?",
                    long=True,
                    required=True,
                ),
            ],
        ),
    },
    # ---- Demo request ------------------------------------------------------
    {
        "slug": "demo-request",
        "name": "Demo Request",
        "description": "Let interested prospects request a product demo and tell you what they care about.",
        "channel": "link",
        "industry": "saas",
        "role": "sales",
        "definition": _definition(
            "Demo Request",
            [
                q_contact_info(
                    "dr_contact",
                    "Tell us how to reach you",
                    fields={
                        "firstName": {"show": True, "required": True},
                        "email": {"show": True, "required": True},
                        "company": {"show": True, "required": True},
                    },
                    required=True,
                ),
                q_multi_select(
                    "dr_interest",
                    "Which capabilities are you most interested in?",
                    [
                        choice("analytics", "Analytics & reporting"),
                        choice("integrations", "Integrations"),
                        choice("automation", "Automation"),
                        choice("collaboration", "Collaboration"),
                        choice("other", "Other", is_other=True),
                    ],
                ),
                q_open_text("dr_notes", "Anything specific you'd like to see?", long=True),
            ],
        ),
    },
    # ---- Post-purchase (eCommerce) ----------------------------------------
    {
        "slug": "post-purchase",
        "name": "Post-Purchase Feedback",
        "description": "Follow up after checkout to measure the buying experience and likelihood to return.",
        "channel": "link",
        "industry": "ecommerce",
        "role": "cs",
        "definition": _definition(
            "Post-Purchase Feedback",
            [
                q_rating(
                    "pp_experience",
                    "How was your overall shopping experience?",
                    range_=5,
                    scale="star",
                    required=True,
                ),
                q_single_select(
                    "pp_again",
                    "How likely are you to buy from us again?",
                    [
                        choice("very", "Very likely"),
                        choice("maybe", "Maybe"),
                        choice("unlikely", "Unlikely"),
                    ],
                    required=True,
                ),
                q_open_text("pp_comment", "What could have made it better?", long=True),
            ],
        ),
    },
    # ---- Employee engagement / satisfaction --------------------------------
    {
        "slug": "employee-engagement",
        "name": "Employee Engagement",
        "description": "A regular pulse on how engaged, supported, and motivated your team feels.",
        "channel": "link",
        "industry": "other",
        "role": "people",
        "definition": _definition(
            "Employee Engagement",
            [
                q_rating(
                    "ee_engaged",
                    "How engaged do you feel at work right now?",
                    range_=5,
                    scale="number",
                    required=True,
                    lower="Not engaged",
                    upper="Highly engaged",
                ),
                q_rating(
                    "ee_support",
                    "How supported do you feel by your manager?",
                    range_=5,
                    scale="number",
                    required=True,
                ),
                q_single_select(
                    "ee_recommend",
                    "Would you recommend working here to a friend?",
                    [
                        choice("yes", "Yes"),
                        choice("maybe", "Maybe"),
                        choice("no", "No"),
                    ],
                    required=True,
                ),
                q_open_text("ee_improve", "What's one thing that would improve your experience?", long=True),
            ],
        ),
    },
    # ---- Pricing / value feedback -----------------------------------------
    {
        "slug": "pricing-feedback",
        "name": "Pricing & Value Feedback",
        "description": "Understand how customers perceive your pricing relative to the value they get.",
        "channel": "app",
        "industry": "saas",
        "role": "marketing",
        "definition": _definition(
            "Pricing & Value Feedback",
            [
                q_single_select(
                    "price_perception",
                    "How do you feel about our pricing?",
                    [
                        choice("toolow", "Too cheap for the value"),
                        choice("fair", "About right"),
                        choice("toohigh", "Too expensive"),
                    ],
                    required=True,
                ),
                q_rating(
                    "price_value",
                    "How would you rate the value you get for the price?",
                    range_=5,
                    scale="star",
                    required=True,
                ),
                q_open_text("price_comment", "What would make our pricing feel fairer?", long=True),
            ],
        ),
    },
    # ---- Newsletter signup -------------------------------------------------
    {
        "slug": "newsletter-signup",
        "name": "Newsletter Signup",
        "description": "Grow your list with a friendly opt-in that captures email and topic interests.",
        "channel": "website",
        "industry": "other",
        "role": "marketing",
        "definition": _definition(
            "Newsletter Signup",
            [
                q_contact_info(
                    "nl_email",
                    "Subscribe to our newsletter",
                    fields={
                        "firstName": {"show": True, "required": False},
                        "email": {"show": True, "required": True},
                    },
                    required=True,
                ),
                q_multi_select(
                    "nl_topics",
                    "Which topics interest you most?",
                    [
                        choice("product", "Product updates"),
                        choice("tips", "Tips & best practices"),
                        choice("events", "Events & webinars"),
                        choice("offers", "Special offers"),
                    ],
                ),
                q_consent(
                    "nl_consent",
                    "Consent",
                    "I agree to receive marketing emails and can unsubscribe at any time.",
                    required=True,
                ),
            ],
            endings=[end_screen("end", "You're subscribed!", subheader="Thanks for joining — keep an eye on your inbox.")],
        ),
    },
    # ---- Support follow-up -------------------------------------------------
    {
        "slug": "support-followup",
        "name": "Support Follow-Up",
        "description": "Close the loop after a support interaction to measure resolution and satisfaction.",
        "channel": "link",
        "industry": "saas",
        "role": "cs",
        "definition": _definition(
            "Support Follow-Up",
            [
                q_single_select(
                    "sf_resolved",
                    "Was your issue fully resolved?",
                    [
                        choice("yes", "Yes, completely"),
                        choice("partly", "Partly"),
                        choice("no", "No"),
                    ],
                    required=True,
                ),
                q_rating(
                    "sf_agent",
                    "How would you rate the support you received?",
                    range_=5,
                    scale="star",
                    required=True,
                ),
                q_open_text("sf_comment", "Anything else you'd like our team to know?", long=True),
            ],
        ),
    },
]


# Fail fast on a duplicate slug (would make get_template ambiguous + break gallery anchors).
_seen_slugs: set[str] = set()
for _entry in CATALOG:
    if _entry["slug"] in _seen_slugs:
        raise ValueError(f"Duplicate template slug in CATALOG: {_entry['slug']!r}")
    _seen_slugs.add(_entry["slug"])
del _entry, _seen_slugs


def get_template(slug: str) -> dict[str, Any] | None:
    """Return the catalog entry with ``slug`` (or ``None`` if unknown)."""
    for entry in CATALOG:
        if entry["slug"] == slug:
            return entry
    return None


def template_definition(slug: str) -> dict[str, Any] | None:
    """A DEEP COPY of the template's survey definition, ready to pass to ``create_survey``.

    Deep-copied because the catalog dicts are module-level singletons: two surveys created
    from the same template must not share question/logic list references (a later mutation of
    one survey would otherwise mutate the template — and the other survey — at runtime).
    """
    entry = get_template(slug)
    if entry is None:
        return None
    return copy.deepcopy(entry["definition"])


def has_logic(definition: dict[str, Any]) -> bool:
    """True if any question in ``definition`` carries a non-empty ``logic`` array.

    Drives the gallery card's "Has logic" branch indicator (WS-14). Pure helper so the
    gallery view can decorate each entry without re-walking the definition in the template.
    """
    for q in definition.get("questions", []):
        if isinstance(q, dict) and q.get("logic"):
            return True
    return False
