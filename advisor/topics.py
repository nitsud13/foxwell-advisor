"""Playbook topics: the change types the advisor knows how to talk about.

Each topic has:
  id        stable key, also the Choice option Jev picks from
  label     short human name
  desc      what Jev sees as the option description when classifying a change
  query     what we search in Foxwell to build the playbook entry
  question  the stance question we ask Jev about every retrieved chunk
"""

TOPICS: list[dict] = [
    {
        "id": "budget_increase_large",
        "label": "Large budget increase",
        "desc": "raising a daily or lifetime budget by more than 20 percent in one step",
        "query": "how much can you increase Meta ad budget without resetting learning phase",
        "queries": ["how much can you increase Meta ad budget without resetting learning phase", "scaling budget too fast learning phase reset Meta", "how fast can you scale a winning ad set Meta budget"],
        "question": "Raising the budget by more than 20 percent in one step is fine",
    },
    {
        "id": "budget_increase_small",
        "label": "Small budget increase",
        "desc": "raising a budget by 20 percent or less",
        "query": "safe daily budget increase step 20 percent Meta ads scaling",
        "queries": ["safe daily budget increase step 20 percent Meta ads scaling", "20 percent rule budget scaling Meta ads", "slow scaling budget increments Meta learning"],
        "question": "Raising the budget in steps of 20 percent or less is the right approach",
    },
    {
        "id": "budget_decrease",
        "label": "Budget decrease",
        "desc": "lowering a daily or lifetime budget",
        "query": "cutting Meta ad set budget effect on delivery and learning",
        "queries": ["cutting Meta ad set budget effect on delivery and learning", "lowering ad set budget Meta performance drop", "reducing spend on a campaign without resetting learning"],
        "question": "Cutting the budget is safe and does not hurt delivery",
    },
    {
        "id": "bid_cost_cap",
        "label": "Switch to cost cap",
        "desc": "changing the bid strategy to cost cap or cost per result goal",
        "query": "cost cap bidding Meta ads when to use results",
        "queries": ["cost cap bidding Meta ads when to use results", "cost cap not spending Meta ads fix", "cost controls Meta ads cost per result goal experience"],
        "question": "Cost cap bidding is a good choice for this account",
    },
    {
        "id": "bid_bid_cap",
        "label": "Switch to bid cap",
        "desc": "changing the bid strategy to a manual bid cap",
        "query": "bid cap strategy Meta ads experience",
        "queries": ["bid cap strategy Meta ads experience", "bid cap Meta ads when it makes sense", "manual bid cap vs cost cap Meta"],
        "question": "Bid cap is a good choice for most accounts",
    },
    {
        "id": "bid_lowest_cost",
        "label": "Switch to lowest cost",
        "desc": "changing the bid strategy back to lowest cost or highest volume",
        "query": "lowest cost highest volume bidding vs cost cap Meta",
        "queries": ["lowest cost highest volume bidding vs cost cap Meta", "highest volume bidding vs cost controls Meta ads", "switching back to lowest cost from cost cap"],
        "question": "Lowest cost bidding is the right default",
    },
    {
        "id": "bid_roas_goal",
        "label": "Set ROAS goal",
        "desc": "changing the bid strategy to a minimum ROAS goal",
        "query": "minimum ROAS goal bidding Meta ads results",
        "queries": ["minimum ROAS goal bidding Meta ads results", "ROAS goal bid strategy not spending Meta", "minimum ROAS bidding results Meta ads"],
        "question": "A minimum ROAS goal bid strategy works well",
    },
    {
        "id": "pause_in_learning",
        "label": "Pause during learning",
        "desc": "pausing or turning off an ad, ad set, or campaign that is still in the learning phase or has little data",
        "query": "pausing ad set during learning phase Meta should you wait",
        "queries": ["pausing ad set during learning phase Meta should you wait", "learning phase how long to wait before turning off ad set", "judging an ad set too early Meta learning phase"],
        "question": "You should wait until learning finishes before pausing",
    },
    {
        "id": "pause_winner",
        "label": "Pause a performing campaign",
        "desc": "pausing an ad, ad set, or campaign that is currently profitable or performing",
        "query": "pausing a winning ad set to relaunch later Meta ads",
        "queries": ["pausing a winning ad set to relaunch later Meta ads", "turning off a winning ad set relaunch later Meta", "pausing and unpausing ad sets performance reset"],
        "question": "Pausing a winning ad set and relaunching later hurts performance",
    },
    {
        "id": "duplicate_adset",
        "label": "Duplicate ad set",
        "desc": "duplicating an ad set or campaign with the same audience and creative",
        "query": "duplicating ad sets same audience auction overlap Meta",
        "queries": ["duplicating ad sets same audience auction overlap Meta", "duplicate ad set auction overlap Meta", "duplicating winning ad set to scale Meta results"],
        "question": "Duplicating an ad set with the same audience causes auction overlap",
    },
    {
        "id": "audience_broad",
        "label": "Go broad",
        "desc": "removing interest or lookalike targeting and going broad or Advantage+ audience",
        "query": "broad targeting vs interests lookalikes Meta ads 2026",
        "queries": ["broad targeting vs interests lookalikes Meta ads 2026", "Advantage+ audience broad targeting results", "going broad no targeting Meta ads 2026"],
        "question": "Broad targeting outperforms interest and lookalike targeting",
    },
    {
        "id": "audience_narrow",
        "label": "Narrow the audience",
        "desc": "adding interests, lookalikes, or exclusions to narrow an audience",
        "query": "narrowing audience interests lookalikes still work Meta",
        "queries": ["narrowing audience interests lookalikes still work Meta", "lookalike audiences still work Meta ads", "interest targeting dead Meta ads"],
        "question": "Narrowing the audience with interests or lookalikes still helps",
    },
    {
        "id": "advantage_plus_shopping",
        "label": "Move to Advantage+ Shopping",
        "desc": "switching prospecting into an Advantage+ Shopping campaign",
        "query": "Advantage+ shopping campaign vs manual prospecting results",
        "queries": ["Advantage+ shopping campaign vs manual prospecting results", "ASC vs manual campaigns results Meta", "Advantage+ shopping campaign prospecting share existing customers"],
        "question": "Moving prospecting into Advantage+ Shopping is a good move",
    },
    {
        "id": "cbo_toggle",
        "label": "Toggle campaign budget",
        "desc": "turning Advantage campaign budget (CBO) on or off",
        "query": "CBO vs ABO campaign budget optimization Meta which is better",
        "queries": ["CBO vs ABO campaign budget optimization Meta which is better", "Advantage campaign budget vs ad set budget Meta", "CBO ABO testing structure Meta ads"],
        "question": "Campaign budget optimization beats ad set budgets",
    },
    {
        "id": "placements_manual",
        "label": "Manual placements",
        "desc": "switching from Advantage+ placements to manual placements",
        "query": "manual placements vs advantage placements Meta ads",
        "queries": ["manual placements vs advantage placements Meta ads", "excluding placements audience network Meta ads", "Advantage+ placements vs manual Meta results"],
        "question": "Manual placements are worth using",
    },
    {
        "id": "dayparting",
        "label": "Ad scheduling",
        "desc": "adding a schedule or dayparting to an ad set",
        "query": "dayparting ad scheduling Meta ads worth it",
        "queries": ["dayparting ad scheduling Meta ads worth it", "ad scheduling lifetime budget Meta ads", "running ads only certain hours Meta"],
        "question": "Dayparting improves results",
    },
    {
        "id": "creative_refresh",
        "label": "Creative refresh",
        "desc": "swapping or adding new creatives to a running ad set",
        "query": "when to refresh creative ad fatigue frequency Meta",
        "queries": ["when to refresh creative ad fatigue frequency Meta", "adding new ads to existing ad set vs new ad set Meta", "creative fatigue frequency refresh Meta ads"],
        "question": "Refreshing creative regularly is necessary to fight ad fatigue",
    },
    {
        "id": "creative_count",
        "label": "Number of ads per ad set",
        "desc": "changing how many ads are live in one ad set",
        "query": "how many ads per ad set Meta best practice",
        "queries": ["how many ads per ad set Meta best practice", "how many creatives per ad set Meta 2026", "too many ads in one ad set delivery Meta"],
        "question": "Running more than six ads in one ad set is fine",
    },
    {
        "id": "attribution_window",
        "label": "Attribution setting",
        "desc": "changing the attribution window such as 7 day click or 1 day view",
        "query": "attribution window setting 7 day click 1 day view Meta optimization",
        "queries": ["attribution window setting 7 day click 1 day view Meta optimization", "7 day click vs 1 day click attribution Meta optimization", "attribution setting change Meta ads impact"],
        "question": "Using 1 day view attribution for optimization is a good idea",
    },
    {
        "id": "geo_expand",
        "label": "Expand geography",
        "desc": "adding new countries or regions to targeting",
        "query": "expanding to new countries same ad set or separate Meta ads",
        "queries": ["expanding to new countries same ad set or separate Meta ads", "expanding to new countries Meta ads same ad set", "international targeting separate campaigns Meta"],
        "question": "Add new countries into the same ad set rather than a separate one",
    },
    {
        "id": "catalog_dpa",
        "label": "Catalog ads",
        "desc": "adding or switching to catalog or dynamic product ads",
        "query": "catalog ads DPA prospecting results Meta",
        "queries": ["catalog ads DPA prospecting results Meta", "catalog ads prospecting Meta results", "dynamic product ads broad audience Meta"],
        "question": "Catalog ads work for prospecting",
    },
    {
        "id": "objective_change",
        "label": "Change objective",
        "desc": "changing the campaign objective or the conversion event being optimized",
        "query": "changing optimization event purchase vs add to cart Meta",
        "queries": ["changing optimization event purchase vs add to cart Meta", "optimizing for add to cart vs purchase Meta", "changing conversion event campaign Meta learning"],
        "question": "Optimizing for a softer event than purchase is a good idea",
    },
    {
        "id": "pause_ad",
        "label": "Turn off an ad",
        "desc": "turning off a single ad or creative inside an ad set, pruning or killing an underperforming ad",
        "query": "when to turn off an ad creative Meta kill underperforming ads",
        "queries": ["when to turn off an ad creative Meta kill underperforming ads", "how long to let an ad run before turning it off Meta", "pruning ads in an ad set effect on delivery Meta"],
        "question": "Turning off an underperforming ad quickly is the right move",
    },
    {
        "id": "unpause_ad",
        "label": "Turn an ad back on",
        "desc": "turning a paused ad or creative back on, relaunching an old ad inside an existing ad set",
        "query": "turning an old ad back on Meta does it keep social proof and learning",
        "queries": ["turning an old ad back on Meta does it keep social proof and learning", "relaunch paused ad vs duplicate new ad Meta", "reactivating paused ads performance Meta"],
        "question": "Turning a paused ad back on works as well as launching it fresh",
    },
    {
        "id": "other",
        "label": "Other change",
        "desc": "a change that does not match any listed type",
        "query": "",
        "question": "",
    },
]

TOPIC_BY_ID = {t["id"]: t for t in TOPICS}


def topic_criteria() -> dict[str, str]:
    """Choice criteria for classifying a change event into a topic."""
    return {t["id"]: t["desc"] for t in TOPICS}
