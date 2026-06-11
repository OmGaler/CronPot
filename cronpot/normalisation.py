from __future__ import annotations

import re
from dataclasses import replace

from cronpot.config import AutomationConfig
from cronpot.models import Recipe


TERM_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("all-purpose flour", "plain flour"),
    ("arugula", "rocket"),
    ("baking sheet", "baking tray"),
    ("bell pepper", "pepper"),
    ("bell peppers", "peppers"),
    ("broiler", "grill"),
    ("broil", "grill"),
    ("cilantro", "coriander"),
    ("confectioners' sugar", "icing sugar"),
    ("confectioners sugar", "icing sugar"),
    ("cookie sheet", "baking tray"),
    ("eggplant", "aubergine"),
    ("ground beef", "minced beef"),
    ("ground chicken", "minced chicken"),
    ("ground lamb", "minced lamb"),
    ("ground turkey", "minced turkey"),
    ("powdered sugar", "icing sugar"),
    ("scallion", "spring onion"),
    ("scallions", "spring onions"),
    ("skillet", "frying pan"),
    ("zucchini", "courgette"),
)

MEAT_KEYWORDS = (
    "beef",
    "brisket",
    "chicken",
    "duck",
    "goose",
    "lamb",
    "meatball",
    "minced beef",
    "salami",
    "sausage",
    "short rib",
    "steak",
    "turkey",
    "veal",
)

DAIRY_KEYWORDS = (
    "butter",
    "cheddar",
    "cheese",
    "cream",
    "creme fraiche",
    "feta",
    "milk",
    "mozzarella",
    "parmesan",
    "yoghurt",
)

CATEGORY_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("Drinks", ("drink", "cocktail", "mojito", "highball", "cider", "steamer", "ale"), ("drink",)),
    ("Sorbets", ("sorbet",), ("sorbet", "dessert")),
    ("Cakes", ("cake", "cupcake", "brownie", "babka", "muffin", "swiss roll"), ("cake", "dessert")),
    (
        "Desserts",
        (
            "dessert",
            "cake",
            "cookie",
            "biscuit",
            "pie",
            "crumble",
            "custard",
            "jam",
            "panna cotta",
            "sweet",
            "chocolate",
            "doughnut",
        ),
        ("dessert",),
    ),
    ("Breads", ("bread", "challah", "focaccia", "bloomer", "pizza dough"), ("bread",)),
    ("Soups", ("soup", "stock", "broth"), ("soup", "starter")),
    (
        "Condiments",
        ("sauce", "jam", "pickle", "pickled", "chutney", "dressing", "butter", "coulis", "molasses"),
        ("condiment",),
    ),
    ("Sides", ("potato", "rice", "sprout", "beans", "kugel", "salad", "side"), ("side",)),
    (
        "Mains",
        ("beef", "chicken", "duck", "lamb", "salmon", "tuna", "tofu", "pasta", "noodle", "burger", "pizza"),
        ("main",),
    ),
    ("Starters", ("starter", "terrine", "carpaccio", "fondue"), ("starter",)),
)


DIETARY_TAGS = ("parev", "milky", "meaty")
UNICODE_FRACTIONS: tuple[tuple[str, str], ...] = (
    ("1/8", "⅛"),
    ("1/4", "¼"),
    ("1/3", "⅓"),
    ("3/8", "⅜"),
    ("1/2", "½"),
    ("5/8", "⅝"),
    ("2/3", "⅔"),
    ("3/4", "¾"),
    ("7/8", "⅞"),
)


def normalise_recipe(recipe: Recipe, config: AutomationConfig | None = None) -> Recipe:
    config = config or AutomationConfig()
    title = normalise_text(recipe.title, config)
    ingredients = [normalise_text(item, config) for item in recipe.ingredients if normalise_text(item, config)]
    steps = [normalise_text(step, config) for step in recipe.steps if normalise_text(step, config)]
    searchable = " ".join([title, *ingredients, *steps]).casefold()

    categories = unique_labels_preserving_order([*recipe.categories, *infer_categories(searchable)])
    tags = unique_preserving_order([*recipe.tags, *infer_category_tags(categories)])
    tags = ensure_dietary_tag(tags, searchable, require=config.require_dietary_tag)

    return replace(
        recipe,
        title=title,
        ingredients=ingredients,
        steps=steps,
        prep_time=normalise_text(recipe.prep_time, config),
        cook_time=normalise_text(recipe.cook_time, config),
        total_time=normalise_text(recipe.total_time, config),
        servings=normalise_text(recipe.servings, config),
        yield_amount=normalise_text(recipe.yield_amount, config),
        tags=tags,
        categories=categories or ["Mains"],
    )


def normalise_text(value: str, config: AutomationConfig | None = None) -> str:
    config = config or AutomationConfig()
    text = str(value or "")
    text = text.replace("\xa0", " ")
    text = text.replace("\u201c", '"').replace("\u201d", '"').replace("\u2019", "'")
    text = re.sub(r"\s+", " ", text).strip()
    if config.english == "british":
        for source, replacement in TERM_REPLACEMENTS:
            text = _replace_word(text, source, replacement)
    if config.fraction_style == "unicode":
        text = _normalise_unicode_fractions(text)
    return text


def infer_categories(searchable_text: str) -> list[str]:
    categories: list[str] = []
    for category, markers, _tags in CATEGORY_RULES:
        if any(_contains_word_or_phrase(searchable_text, marker) for marker in markers):
            categories.append(category)
    if "Soups" in categories and "Starters" not in categories:
        categories.append("Starters")
    if "Sorbets" in categories and "Desserts" not in categories:
        categories.append("Desserts")
    return unique_labels_preserving_order(categories)


def infer_category_tags(categories: list[str]) -> list[str]:
    tags: list[str] = []
    category_keys = {category.casefold() for category in categories}
    for category, _markers, category_tags in CATEGORY_RULES:
        if category.casefold() in category_keys:
            tags.extend(category_tags)
    return unique_preserving_order(tags)


def infer_dietary_tags(searchable_text: str) -> list[str]:
    text = re.sub(r"\b(?:vegan|plant[- ]based|coconut)\s+butter\b", "", searchable_text)
    text = re.sub(r"\bcoconut\s+milk\b", "", text)

    has_meat = any(_contains_word_or_phrase(text, keyword) for keyword in MEAT_KEYWORDS)
    has_dairy = any(_contains_word_or_phrase(text, keyword) for keyword in DAIRY_KEYWORDS)

    if has_meat:
        return ["meaty"]
    if has_dairy:
        return ["milky"]
    return ["parev"]


def ensure_dietary_tag(tags: list[str], searchable_text: str, require: bool = True) -> list[str]:
    if not require:
        return tags

    non_dietary_tags = [tag for tag in tags if tag not in DIETARY_TAGS]
    existing = [tag for tag in tags if tag in DIETARY_TAGS]
    dietary_tag = existing[0] if len(existing) == 1 else infer_dietary_tags(searchable_text)[0]
    return unique_preserving_order([*non_dietary_tags, dietary_tag])


def unique_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        clean = re.sub(r"[^a-z0-9_-]+", "-", value.strip().casefold()).strip("-")
        if clean and clean not in seen:
            unique.append(clean)
            seen.add(clean)
    return unique


def unique_labels_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        clean = re.sub(r"\s+", " ", value.strip())
        key = clean.casefold()
        if clean and key not in seen:
            unique.append(clean)
            seen.add(key)
    return unique


def _replace_word(text: str, source: str, replacement: str) -> str:
    pattern = re.compile(rf"\b{re.escape(source)}\b", flags=re.IGNORECASE)
    return pattern.sub(lambda match: _match_case(match.group(0), replacement), text)


def _match_case(original: str, replacement: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement.capitalize()
    return replacement


def _contains_word_or_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"\b{re.escape(phrase.casefold())}\b", text) is not None


def _normalise_unicode_fractions(text: str) -> str:
    for source, replacement in UNICODE_FRACTIONS:
        text = re.sub(rf"(\d+)\s+{re.escape(source)}(?![\d/])", rf"\1{replacement}", text)
        text = re.sub(rf"(?<![\d/]){re.escape(source)}(?![\d/])", replacement, text)
    return text
