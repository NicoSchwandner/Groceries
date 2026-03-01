"""Weekly grocery workflow: Mela → translate → checklist → ICA."""

import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from functools import partial
from typing import Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MELA_DB = os.path.expanduser(
    "~/Library/Group Containers/66JC38RDUD.recipes.mela/Data/Curcuma.sqlite"
)
INGREDIENT_CACHE = os.path.join(SCRIPT_DIR, "ingredient_cache.json")
SHOPPING_LIST_MD = os.path.join(SCRIPT_DIR, "inkopslista.md")

ICA_USER_INFO_URL = "https://www.ica.se/api/user/information"
ICA_API_BASE = "https://apimgw-pub.ica.se/sverige/digx/shopping-list/v1/api"

UNIT_MAP = {
    "tablespoon": "msk", "tablespoons": "msk", "tbsp": "msk",
    "teaspoon": "tsk", "teaspoons": "tsk", "tsp": "tsk",
    "cup": "dl", "cups": "dl",
    "clove": "klyfta", "cloves": "klyftor",
    "bunch": "bunt", "pinch": "nypa",
    "can": "burk", "block": "block", "packet": "förp", "package": "förp",
}


# ---------------------------------------------------------------------------
# .env loader
# ---------------------------------------------------------------------------
def load_env():
    env = {}
    path = os.path.join(SCRIPT_DIR, ".env")
    if not os.path.exists(path):
        return env
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


# ---------------------------------------------------------------------------
# Mela DB
# ---------------------------------------------------------------------------
def read_mela_recipes():
    """Read 'Want to Cook' recipes from Mela's SQLite DB."""
    if not os.path.exists(MELA_DB):
        print(f"ERROR: Mela database not found at {MELA_DB}")
        sys.exit(1)

    conn = sqlite3.connect(f"file:{MELA_DB}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT ZTITLE, ZINGREDIENTS, ZYIELD FROM ZRECIPEOBJECT WHERE ZWANTTOCOOK = 1"
    ).fetchall()
    conn.close()

    recipes = []
    for title, ingredients_raw, servings in rows:
        ingredients = parse_ingredients(ingredients_raw or "")
        recipes.append({
            "title": title,
            "servings": servings or "",
            "ingredients": ingredients,
        })
    return recipes


UNICODE_FRACTIONS = {"¼": "0.25", "½": "0.5", "¾": "0.75", "⅓": "0.33", "⅔": "0.67"}

ALL_UNITS = (
    # Swedish
    "st|msk|tsk|dl|ml|cl|l|g|kg|krm|port|förp|burk|klyfta|klyftor|bunt|nypa|huvud",
    # English
    "tablespoons?|teaspoons?|tbsp|tsp|cups?|cloves?|bunch|can|cans|block|blocks"
    "|packets?|packages?|oz|lb|lbs|pieces?",
)
UNIT_RE = re.compile(
    r"^(?P<qty>\d+[\.,/]?\d*(?:\s*[-–]\s*\d+[\.,/]?\d*)?)\s*"
    rf"(?P<unit>{'|'.join(ALL_UNITS)})?\s+"
    r"(?P<name>.+)$",
    re.IGNORECASE,
)
# Handle "400g" without space
UNIT_NOSPACE_RE = re.compile(
    r"^(?P<qty>\d+[\.,]?\d*)(?P<unit>ml|cl|dl|kg|oz|lb|g|l)\s+(?P<name>.+)$",
    re.IGNORECASE,
)


def parse_ingredient_line(raw: str) -> dict:
    """Parse a single ingredient line into {raw, name, qty, unit}."""
    line = raw.strip()
    # Replace unicode fractions
    for frac, num in UNICODE_FRACTIONS.items():
        line = line.replace(frac, num)

    m = UNIT_RE.match(line) or UNIT_NOSPACE_RE.match(line)
    if m:
        name = m.group("name").strip()
        # Strip parenthetical notes like "(diced)" or "(I use...)"
        name = re.sub(r"\s*\([^)]*\)\s*$", "", name)
        # Strip trailing prep notes after comma
        name = re.sub(r",\s*(finely |roughly )?(chopped|diced|grated|sliced|minced|crushed|pressed).*$", "", name, flags=re.IGNORECASE)
        unit = (m.group("unit") or "").strip()
        # Normalize English units to Swedish
        unit_lower = unit.lower()
        unit = UNIT_MAP.get(unit_lower, unit)
        return {"raw": raw, "name": name.strip(), "qty": m.group("qty").strip(), "unit": unit}

    # No quantity found — entire line is the ingredient name
    name = re.sub(r"\s*\([^)]*\)\s*$", "", line)
    name = re.sub(r",\s*(finely |roughly )?(chopped|diced|grated|sliced|minced|crushed|pressed).*$", "", name, flags=re.IGNORECASE)
    return {"raw": raw, "name": name.strip(), "qty": "", "unit": ""}


def parse_ingredients(raw: str) -> list[dict]:
    """Parse Mela's newline-separated ingredient format."""
    items = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        items.append(parse_ingredient_line(line))
    return items


# ---------------------------------------------------------------------------
# Translation cache (name → swedish name + category, no quantities)
# ---------------------------------------------------------------------------
def load_cache() -> dict:
    if os.path.exists(INGREDIENT_CACHE):
        with open(INGREDIENT_CACHE) as f:
            return json.load(f)
    return {}


def save_cache(cache: dict):
    with open(INGREDIENT_CACHE, "w") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)


def translate_ingredients(ingredient_names: list[str], api_key: str) -> dict[str, dict]:
    """Translate ingredient names to Swedish. Returns {name -> {swedish_name, category}}."""
    cache = load_cache()
    uncached = [n for n in ingredient_names if n not in cache]

    if uncached:
        print(f"Translating {len(uncached)} new ingredients via OpenAI...")
        batch_size = 50
        for i in range(0, len(uncached), batch_size):
            batch = uncached[i : i + batch_size]
            results = call_openai(batch, api_key)
            cache.update(results)
        save_cache(cache)
    else:
        print("All ingredients found in cache.")

    return {n: cache[n] for n in ingredient_names if n in cache}


def call_openai(ingredient_names: list[str], api_key: str) -> dict[str, dict]:
    """Send ingredient names to OpenAI for translation. No quantities — just names."""
    numbered = "\n".join(f"{i+1}. {name}" for i, name in enumerate(ingredient_names))
    prompt = f"""Translate these ingredient names to Swedish. For each, return:
- "swedish_name": the ingredient name in Swedish (lowercase)
- "category": one of: "produce", "protein", "dairy", "pantry", "frozen", "spice", "bread", "condiment"

Rules:
- Translate ALL names to Swedish, even if already Swedish (just lowercase it)
- Strip preparation notes (chopped, diced, grated, fryst, etc.) — just the base ingredient
- "salt and pepper", "salt & svartpeppar" etc. → return the entry as-is (we'll handle splitting elsewhere)
- Keep it simple: "vitlöksklyftor" → "vitlök", "garlic cloves" → "vitlök"
- For compound ingredients, use the common Swedish grocery name

Ingredient names:
{numbered}

Return a JSON object where keys are the EXACT original strings and values are objects with swedish_name and category. Return ONLY valid JSON, no markdown."""

    payload = json.dumps({
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())

    content = data["choices"][0]["message"]["content"]
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)
    return json.loads(content)


# ---------------------------------------------------------------------------
# Consolidation
# ---------------------------------------------------------------------------
CATEGORY_ORDER = {
    "bread": ("Pasta, nudlar & bröd", 0),
    "protein": ("Protein", 1),
    "dairy": ("Mejeri & kyl", 2),
    "produce": ("Grönsaker & frukt", 3),
    "frozen": ("Fryst", 4),
    "condiment": ("Konserver & såser", 5),
    "spice": ("Kryddor & skafferi", 6),
    "pantry": ("Skafferi", 7),
}


VOLUME_TO_ML = {"ml": 1, "cl": 10, "dl": 100, "l": 1000}
WEIGHT_TO_G = {"g": 1, "kg": 1000}


def _best_volume_unit(ml: float) -> tuple[float, str]:
    if ml >= 1000 and ml % 1000 == 0:
        return ml / 1000, "l"
    if ml >= 100:
        return ml / 100, "dl"
    return ml, "ml"


def _best_weight_unit(g: float) -> tuple[float, str]:
    if g >= 1000:
        return g / 1000, "kg"
    return g, "g"


def consolidate(recipes, translations):
    # type: (list[dict], dict) -> tuple[list[str], list[dict]]
    """Merge ingredients across recipes, grouping by category.

    Returns (recipe_titles, sections) where each item carries per-recipe
    sources so the JS checklist can apply multipliers dynamically.
    """
    recipe_titles = [r["title"] for r in recipes]
    merged = {}  # swedish_name -> {sources, category}

    for recipe_idx, recipe in enumerate(recipes):
        for ing in recipe["ingredients"]:
            t = translations.get(ing["name"])
            if not t:
                continue

            swedish_name = t["swedish_name"].lower().strip()
            qty = ing.get("qty", "")
            unit = ing.get("unit", "")

            if swedish_name not in merged:
                merged[swedish_name] = {
                    "sources": [],
                    "category": t.get("category", "pantry"),
                }
            merged[swedish_name]["sources"].append({
                "recipeIdx": recipe_idx,
                "qty": qty,
                "unit": unit,
            })

    # Build sectioned output
    sections = {}
    for name, data in sorted(merged.items()):
        cat = data["category"]
        section_name, order = CATEGORY_ORDER.get(cat, ("Övrigt", 99))
        if section_name not in sections:
            sections[section_name] = {"order": order, "items": []}

        item_id = re.sub(r"[^a-z0-9]", "_", name)

        sections[section_name]["items"].append({
            "id": item_id,
            "name": name.capitalize(),
            "sources": data["sources"],
        })

    result = [
        {"section": name, "items": sec["items"]}
        for name, sec in sorted(sections.items(), key=lambda x: x[1]["order"])
    ]
    return recipe_titles, result


def combine_quantities(qty_units: list[tuple[str, str]]) -> str:
    """Sum quantities with unit conversion (ml/dl/l, g/kg). Unparseable values joined with +."""
    if not qty_units:
        return ""

    total_ml = 0
    total_g = 0
    other_totals = {}  # unit -> total (for msk, tsk, st, etc.)
    unparseable = []

    for qty_str, unit in qty_units:
        normalized = qty_str.replace(",", ".")
        unit_lower = unit.lower()
        try:
            value = float(normalized)
        except ValueError:
            unparseable.append(f"{qty_str} {unit}".strip())
            continue

        if unit_lower in VOLUME_TO_ML:
            total_ml += value * VOLUME_TO_ML[unit_lower]
        elif unit_lower in WEIGHT_TO_G:
            total_g += value * WEIGHT_TO_G[unit_lower]
        else:
            other_totals[unit] = other_totals.get(unit, 0) + value

    parts = []
    if total_ml > 0:
        val, u = _best_volume_unit(total_ml)
        display = str(val).rstrip("0").rstrip(".")
        parts.append(f"{display} {u}")
    if total_g > 0:
        val, u = _best_weight_unit(total_g)
        display = str(val).rstrip("0").rstrip(".")
        parts.append(f"{display} {u}")
    for unit, total in sorted(other_totals.items()):
        display = str(total).rstrip("0").rstrip(".")
        parts.append(f"{display} {unit}".strip())
    parts.extend(unparseable)

    return " + ".join(parts)


# ---------------------------------------------------------------------------
# Local server + HTML checklist
# ---------------------------------------------------------------------------
def build_html(recipe_titles, sections, port):
    """Generate the interactive HTML shopping list."""
    recipes_json = json.dumps(recipe_titles, ensure_ascii=False)
    data_json = json.dumps(sections, ensure_ascii=False, indent=2)
    run_id = hashlib.md5(data_json.encode()).hexdigest()[:8]
    save_url = f"http://localhost:{port}/save"

    return f"""<!DOCTYPE html>
<html lang="sv">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Inköpslista</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; color: #1a1a1a; max-width: 700px; margin: 0 auto; padding: 16px; }}
    h1 {{ font-size: 1.5rem; margin-bottom: 4px; }}
    .subtitle {{ color: #666; font-size: 0.9rem; margin-bottom: 20px; }}
    h2 {{ font-size: 1.1rem; margin: 20px 0 8px; padding-bottom: 4px; border-bottom: 2px solid #e0e0e0; }}
    .item {{ display: flex; align-items: center; gap: 10px; padding: 8px 12px; background: white; border-radius: 8px; margin-bottom: 4px; cursor: pointer; transition: opacity 0.2s; }}
    .item:hover {{ background: #fafafa; }}
    .item.checked {{ opacity: 0.4; }}
    .item.checked .name {{ text-decoration: line-through; }}
    .item input[type="checkbox"] {{ width: 20px; height: 20px; accent-color: #4caf50; flex-shrink: 0; cursor: pointer; }}
    .name {{ flex: 1; font-size: 0.95rem; display: flex; align-items: center; gap: 4px; }}
    .name-edited {{ color: #e65100; font-weight: 600; }}
    .name-edit {{ color: #ccc; font-size: 0.75rem; cursor: pointer; padding: 2px; }}
    .name-edit:hover {{ color: #666; }}
    .name-input {{ flex: 1; padding: 2px 6px; border: 1px solid #4caf50; border-radius: 4px; font-size: 0.95rem; color: #333; outline: none; }}
    .qty {{ color: #666; font-size: 0.85rem; white-space: nowrap; cursor: text; }}
    .qty-input {{ width: 80px; padding: 2px 6px; border: 1px solid #4caf50; border-radius: 4px; font-size: 0.85rem; color: #333; outline: none; text-align: right; }}
    .qty-edited {{ color: #e65100; font-weight: 600; }}
    .recipes {{ color: #999; font-size: 0.75rem; font-style: italic; margin-left: 4px; }}
    .actions {{ position: sticky; bottom: 0; background: #f5f5f5; padding: 12px 0; display: flex; gap: 8px; flex-wrap: wrap; }}
    button {{ padding: 10px 20px; border: none; border-radius: 8px; font-size: 0.95rem; cursor: pointer; font-weight: 500; }}
    .btn-primary {{ background: #4caf50; color: white; flex: 1; }}
    .btn-primary:hover {{ background: #43a047; }}
    .btn-secondary {{ background: #e0e0e0; color: #333; }}
    .btn-secondary:hover {{ background: #d0d0d0; }}
    .counter {{ text-align: center; color: #666; font-size: 0.85rem; margin-top: 8px; }}
    .section-toggle {{ display: flex; justify-content: space-between; align-items: center; }}
    .check-all {{ font-size: 0.75rem; color: #4caf50; padding: 2px 8px; border-radius: 4px; background: #e8f5e9; cursor: pointer; }}
    .check-all:hover {{ background: #c8e6c9; }}
    .add-bar {{ display: flex; gap: 8px; margin-bottom: 16px; }}
    .add-bar input {{ flex: 1; padding: 10px 12px; border: 1px solid #ddd; border-radius: 8px; font-size: 0.95rem; background: white; }}
    .add-bar input::placeholder {{ color: #aaa; }}
    .add-bar button {{ padding: 10px 16px; border: none; border-radius: 8px; background: #4caf50; color: white; font-size: 0.95rem; cursor: pointer; white-space: nowrap; }}
    .add-bar button:hover {{ background: #43a047; }}
    .custom-remove {{ color: #ccc; font-size: 0.8rem; cursor: pointer; padding: 2px 6px; border-radius: 4px; }}
    .custom-remove:hover {{ color: #e53935; background: #fce4ec; }}
    .toast {{ position: fixed; top: 20px; left: 50%; transform: translateX(-50%); background: #333; color: white; padding: 12px 24px; border-radius: 8px; display: none; z-index: 10; }}
    .toast.show {{ display: block; }}
    #recipes {{ margin-bottom: 16px; }}
    .recipe-row {{ display: flex; align-items: center; justify-content: space-between; padding: 6px 12px; background: white; border-radius: 8px; margin-bottom: 4px; }}
    .recipe-name {{ font-size: 0.9rem; font-weight: 500; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .mult-btns {{ display: flex; gap: 4px; flex-shrink: 0; }}
    .mult-btn {{ padding: 4px 10px; border: 1px solid #ddd; border-radius: 6px; background: #f5f5f5; font-size: 0.8rem; cursor: pointer; }}
    .mult-btn.active {{ background: #4caf50; color: white; border-color: #4caf50; }}
    .mult-btn.active-zero {{ background: #e0e0e0; color: #999; border-color: #ccc; }}
    .mult-btn:hover:not(.active):not(.active-zero) {{ background: #e8e8e8; }}
    .staple-btn {{ color: #ccc; font-size: 0.8rem; cursor: pointer; padding: 2px; flex-shrink: 0; }}
    .staple-btn:hover {{ color: #666; }}
    .staple-btn.active {{ color: #2196f3; }}
  </style>
</head>
<body>
  <h1>Inköpslista</h1>
  <p class="subtitle">Bocka av det du redan har hemma. Resten blir din inköpslista.</p>
  <div id="recipes"></div>
  <div class="add-bar">
    <input type="text" id="addInput" placeholder="Lägg till vara..." autocomplete="off">
    <button onclick="addCustomItem()">Lägg till</button>
  </div>
  <div id="list"></div>
  <div class="counter" id="counter"></div>
  <div class="actions">
    <button class="btn-primary" onclick="saveList()">Spara inköpslista</button>
    <button class="btn-secondary" onclick="uncheckAll()">Nollställ</button>
  </div>
  <div class="toast" id="toast"></div>

<script>
const RECIPES = {recipes_json};
const DATA = {data_json};
const RUN_ID = "{run_id}";
const SAVE_URL = "{save_url}";

const VOLUME_TO_ML = {{ml: 1, cl: 10, dl: 100, l: 1000}};
const WEIGHT_TO_G = {{g: 1, kg: 1000}};

function bestVolumeUnit(ml) {{
  if (ml >= 1000 && ml % 1000 === 0) return [ml / 1000, 'l'];
  if (ml >= 100) return [ml / 100, 'dl'];
  return [ml, 'ml'];
}}

function bestWeightUnit(g) {{
  if (g >= 1000) return [g / 1000, 'kg'];
  return [g, 'g'];
}}

function fmtNum(v) {{
  return parseFloat(v.toFixed(2)).toString();
}}

function combineQuantities(sources, multipliers) {{
  let totalMl = 0, totalG = 0;
  const otherTotals = {{}};
  const unparseable = [];

  for (const s of sources) {{
    if (!s.qty) continue;
    const mult = multipliers[s.recipeIdx] || 0;
    if (mult === 0) continue;
    const normalized = s.qty.replace(',', '.');
    const value = parseFloat(normalized);
    if (isNaN(value)) {{
      unparseable.push(`${{s.qty}} ${{s.unit}}`.trim());
      continue;
    }}
    const scaled = value * mult;
    const u = s.unit.toLowerCase();
    if (u in VOLUME_TO_ML) totalMl += scaled * VOLUME_TO_ML[u];
    else if (u in WEIGHT_TO_G) totalG += scaled * WEIGHT_TO_G[u];
    else {{
      const key = s.unit || '';
      otherTotals[key] = (otherTotals[key] || 0) + scaled;
    }}
  }}

  const parts = [];
  if (totalMl > 0) {{ const [v, u] = bestVolumeUnit(totalMl); parts.push(`${{fmtNum(v)}} ${{u}}`); }}
  if (totalG > 0) {{ const [v, u] = bestWeightUnit(totalG); parts.push(`${{fmtNum(v)}} ${{u}}`); }}
  for (const [unit, total] of Object.entries(otherTotals).sort()) {{
    parts.push(`${{fmtNum(total)}} ${{unit}}`.trim());
  }}
  parts.push(...unparseable);
  return parts.join(' + ');
}}

function isItemVisible(item, multipliers) {{
  return item.sources.some(s => (multipliers[s.recipeIdx] || 0) > 0);
}}

function getRecipeNames(sources, multipliers) {{
  const names = [];
  const seen = new Set();
  for (const s of sources) {{
    if ((multipliers[s.recipeIdx] || 0) > 0 && !seen.has(s.recipeIdx)) {{
      seen.add(s.recipeIdx);
      names.push(RECIPES[s.recipeIdx]);
    }}
  }}
  return names.sort().join(', ');
}}

let staples = new Set(JSON.parse(localStorage.getItem('grocery-staples') || '[]'));
const prevRunId = localStorage.getItem('grocery-run-id');
const isNewRun = prevRunId !== RUN_ID;

let checked, customItems, adjustedQty, adjustedNames, multipliers;
if (isNewRun) {{
  checked = {{}};
  customItems = [];
  adjustedQty = {{}};
  adjustedNames = {{}};
  multipliers = RECIPES.map(() => 1);
  // Pre-check staples that exist in current list
  const allIds = new Set();
  for (const s of DATA) for (const i of s.items) allIds.add(i.id);
  for (const id of staples) {{ if (allIds.has(id)) checked[id] = true; }}
  localStorage.setItem('grocery-run-id', RUN_ID);
  localStorage.setItem('grocery-checked', JSON.stringify(checked));
  localStorage.setItem('grocery-custom', JSON.stringify(customItems));
  localStorage.setItem('grocery-adjusted-qty', JSON.stringify(adjustedQty));
  localStorage.setItem('grocery-adjusted-names', JSON.stringify(adjustedNames));
  localStorage.setItem('grocery-multipliers', JSON.stringify(multipliers));
}} else {{
  checked = JSON.parse(localStorage.getItem('grocery-checked') || '{{}}');
  customItems = JSON.parse(localStorage.getItem('grocery-custom') || '[]');
  adjustedQty = JSON.parse(localStorage.getItem('grocery-adjusted-qty') || '{{}}');
  adjustedNames = JSON.parse(localStorage.getItem('grocery-adjusted-names') || '{{}}');
  multipliers = JSON.parse(localStorage.getItem('grocery-multipliers') || 'null');
  if (!multipliers || multipliers.length !== RECIPES.length) {{
    multipliers = RECIPES.map(() => 1);
  }}
}}

function saveAdjusted() {{
  localStorage.setItem('grocery-adjusted-qty', JSON.stringify(adjustedQty));
}}

function saveAdjustedNames() {{
  localStorage.setItem('grocery-adjusted-names', JSON.stringify(adjustedNames));
}}

function saveStaples() {{
  localStorage.setItem('grocery-staples', JSON.stringify([...staples]));
}}

function toggleStaple(id) {{
  if (staples.has(id)) {{
    staples.delete(id);
  }} else {{
    staples.add(id);
    checked[id] = true;
    save();
  }}
  saveStaples(); render();
}}

function saveMultipliers() {{
  localStorage.setItem('grocery-multipliers', JSON.stringify(multipliers));
}}

function setMultiplier(idx, value) {{
  multipliers[idx] = value;
  saveMultipliers();
  render();
}}

function renderRecipes() {{
  const el = document.getElementById('recipes');
  el.innerHTML = RECIPES.map((name, idx) => `
    <div class="recipe-row">
      <span class="recipe-name">${{name}}</span>
      <div class="mult-btns">
        ${{[0, 1, 2, 3].map(m => {{
          const isActive = multipliers[idx] === m;
          const cls = isActive ? (m === 0 ? 'mult-btn active-zero' : 'mult-btn active') : 'mult-btn';
          return `<button class="${{cls}}" onclick="setMultiplier(${{idx}}, ${{m}})">\u00d7${{m}}</button>`;
        }}).join('')}}
      </div>
    </div>
  `).join('');
}}

function saveCustom() {{
  localStorage.setItem('grocery-custom', JSON.stringify(customItems));
}}

function addCustomItem() {{
  const input = document.getElementById('addInput');
  const name = input.value.trim();
  if (!name) return;
  const id = 'custom_' + name.toLowerCase().replace(/[^a-zåäö0-9]/g, '_');
  if (!customItems.find(i => i.id === id)) {{
    customItems.push({{ id, name }});
    saveCustom();
  }}
  input.value = '';
  render();
}}

function removeCustomItem(id) {{
  customItems = customItems.filter(i => i.id !== id);
  delete checked[id];
  saveCustom(); save(); render();
}}

document.addEventListener('DOMContentLoaded', () => {{
  document.getElementById('addInput').addEventListener('keydown', (e) => {{
    if (e.key === 'Enter') addCustomItem();
  }});
}});

function showToast(msg, ms = 2000) {{
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), ms);
}}

function render() {{
  renderRecipes();
  const list = document.getElementById('list');
  list.innerHTML = '';
  let totalItems = 0, checkedItems = 0;

  const allSections = [...DATA];
  if (customItems.length > 0) {{
    allSections.push({{ section: 'Övrigt', items: customItems.map(i => ({{ ...i, sources: [], custom: true }})) }});
  }}

  for (const section of allSections) {{
    const visibleItems = section.items.filter(i => i.custom || isItemVisible(i, multipliers));
    if (visibleItems.length === 0) continue;

    const sectionEl = document.createElement('div');
    const allChecked = visibleItems.every(i => checked[i.id]);

    sectionEl.innerHTML = `<div class="section-toggle">
      <h2>${{section.section}}</h2>
      <span class="check-all">${{allChecked ? 'Avmarkera alla' : 'Har alla'}}</span>
    </div>`;

    sectionEl.querySelector('.check-all').addEventListener('click', (e) => {{
      e.stopPropagation();
      const allNowChecked = visibleItems.every(i => checked[i.id]);
      visibleItems.forEach(i => {{ checked[i.id] = !allNowChecked; }});
      save(); render();
    }});

    for (const item of visibleItems) {{
      totalItems++;
      if (checked[item.id]) checkedItems++;
      const computedQty = item.custom ? '' : combineQuantities(item.sources, multipliers);
      const recipeNames = item.custom ? '' : getRecipeNames(item.sources, multipliers);
      const displayQty = adjustedQty[item.id] !== undefined ? adjustedQty[item.id] : computedQty;
      const isEdited = adjustedQty[item.id] !== undefined && adjustedQty[item.id] !== computedQty;
      const displayName = adjustedNames[item.id] || item.name;
      const nameEdited = adjustedNames[item.id] && adjustedNames[item.id] !== item.name;
      const div = document.createElement('div');
      div.className = 'item' + (checked[item.id] ? ' checked' : '');
      div.innerHTML = `
        <input type="checkbox" ${{checked[item.id] ? 'checked' : ''}}>
        <span class="name"><span class="name-text${{nameEdited ? ' name-edited' : ''}}">${{displayName}}</span><span class="name-edit">&#x270E;</span></span>
        ${{computedQty || adjustedQty[item.id] ? `<span class="qty${{isEdited ? ' qty-edited' : ''}}">${{displayQty}}</span>` : ''}}
        ${{recipeNames ? `<span class="recipes">${{recipeNames}}</span>` : ''}}
        ${{!item.custom ? `<span class="staple-btn${{staples.has(item.id) ? ' active' : ''}}" title="${{staples.has(item.id) ? 'Har inte alltid hemma' : 'Har alltid hemma'}}">&#x1F3E0;</span>` : ''}}
        ${{item.custom ? `<span class="custom-remove" data-id="${{item.id}}">ta bort</span>` : ''}}
      `;
      div.querySelector('.name-edit').addEventListener('click', (e) => {{
        e.stopPropagation();
        const nameSpan = div.querySelector('.name');
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'name-input';
        input.value = displayName;
        const commit = () => {{
          const val = input.value.trim();
          if (!val || val === item.name) {{
            delete adjustedNames[item.id];
          }} else {{
            adjustedNames[item.id] = val;
          }}
          saveAdjustedNames(); render();
        }};
        input.addEventListener('blur', commit);
        input.addEventListener('keydown', (ev) => {{
          if (ev.key === 'Enter') input.blur();
          if (ev.key === 'Escape') {{ input.value = item.name; input.blur(); }}
        }});
        nameSpan.replaceWith(input);
        input.focus();
        input.select();
      }});
      div.addEventListener('click', (e) => {{
        if (e.target.tagName === 'INPUT' || e.target.classList.contains('custom-remove') || e.target.classList.contains('qty') || e.target.classList.contains('qty-input') || e.target.classList.contains('name-edit') || e.target.classList.contains('staple-btn')) return;
        checked[item.id] = !checked[item.id];
        save(); render();
      }});
      div.querySelector('input[type="checkbox"]').addEventListener('change', () => {{
        checked[item.id] = !checked[item.id];
        save(); render();
      }});
      const qtyEl = div.querySelector('.qty');
      if (qtyEl) {{
        qtyEl.addEventListener('click', (e) => {{
          e.stopPropagation();
          const input = document.createElement('input');
          input.type = 'text';
          input.className = 'qty-input';
          input.value = displayQty;
          input.style.width = Math.max(60, displayQty.length * 9 + 20) + 'px';
          const commit = () => {{
            const val = input.value.trim();
            if (val === '' || val === computedQty) {{
              delete adjustedQty[item.id];
            }} else {{
              adjustedQty[item.id] = val;
            }}
            saveAdjusted(); render();
          }};
          input.addEventListener('blur', commit);
          input.addEventListener('keydown', (ev) => {{
            if (ev.key === 'Enter') input.blur();
            if (ev.key === 'Escape') {{ input.value = computedQty || ''; input.blur(); }}
          }});
          qtyEl.replaceWith(input);
          input.focus();
          input.select();
        }});
      }}
      const stapleBtn = div.querySelector('.staple-btn');
      if (stapleBtn) stapleBtn.addEventListener('click', (e) => {{ e.stopPropagation(); toggleStaple(item.id); }});
      const removeBtn = div.querySelector('.custom-remove');
      if (removeBtn) removeBtn.addEventListener('click', () => removeCustomItem(item.id));
      sectionEl.appendChild(div);
    }}
    list.appendChild(sectionEl);
  }}

  const toBuy = totalItems - checkedItems;
  document.getElementById('counter').textContent = `${{toBuy}} varor att köpa — ${{checkedItems}} finns hemma`;
}}

function save() {{
  localStorage.setItem('grocery-checked', JSON.stringify(checked));
}}

function uncheckAll() {{
  adjustedQty = {{}};
  adjustedNames = {{}};
  multipliers = RECIPES.map(() => 1);
  // Re-check staple items only
  checked = {{}};
  const allIds = new Set();
  for (const s of DATA) for (const i of s.items) allIds.add(i.id);
  for (const id of staples) {{ if (allIds.has(id)) checked[id] = true; }}
  save(); saveAdjusted(); saveAdjustedNames(); saveMultipliers(); render();
}}

async function saveList() {{
  const allSections = [...DATA];
  if (customItems.length > 0) {{
    allSections.push({{ section: 'Övrigt', items: customItems.map(i => ({{ ...i, sources: [], custom: true }})) }});
  }}

  const lines = [];
  for (const section of allSections) {{
    const visible = section.items.filter(i => i.custom || isItemVisible(i, multipliers));
    const needed = visible.filter(i => !checked[i.id]);
    if (needed.length === 0) continue;
    lines.push(`## ${{section.section}}`);
    for (const item of needed) {{
      const computedQty = item.custom ? '' : combineQuantities(item.sources, multipliers);
      const finalQty = adjustedQty[item.id] !== undefined ? adjustedQty[item.id] : computedQty;
      const finalName = adjustedNames[item.id] || item.name;
      const qty = finalQty ? ` — ${{finalQty}}` : '';
      lines.push(`- ${{finalName}}${{qty}}`);
    }}
    lines.push('');
  }}

  if (lines.length === 0) {{
    alert('Alla varor är avbockade! Inget att köpa.');
    return;
  }}

  const content = `# Inköpslista\\n\\n${{lines.join('\\n')}}`;
  try {{
    const resp = await fetch(SAVE_URL, {{
      method: 'POST',
      headers: {{ 'Content-Type': 'text/plain' }},
      body: content,
    }});
    const result = await resp.json();
    showToast(result.message);
  }} catch (e) {{
    showToast('Kunde inte spara — kör weekly.py igen?', 3000);
  }}
}}

render();
</script>
</body>
</html>"""


class ChecklistHandler(BaseHTTPRequestHandler):
    """Serves the checklist HTML and handles save requests."""

    def __init__(self, html: str, save_path: str, *args, **kwargs):
        self.html = html
        self.save_path = save_path
        super().__init__(*args, **kwargs)

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(self.html.encode())

    def do_POST(self):
        if self.path != "/save":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        with open(self.save_path, "w") as f:
            f.write(body)

        n_items = body.count("\n- ")
        response = json.dumps({"message": f"Sparad! {n_items} varor i inkopslista.md"})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(response.encode())
        print(f"\n  Saved {n_items} items to {self.save_path}")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress request logs


def serve_checklist(recipe_titles, sections):
    """Start local server, open browser, wait for user to save."""
    port = 8741
    html = build_html(recipe_titles, sections, port)
    handler = partial(ChecklistHandler, html, SHOPPING_LIST_MD)
    server = HTTPServer(("127.0.0.1", port), handler)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = f"http://localhost:{port}"
    print(f"\nChecklist running at {url}")
    os.system(f"open '{url}'")

    print("Press Enter when done (or Ctrl+C to quit)...")
    try:
        input()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# ICA push
# ---------------------------------------------------------------------------
def ica_authenticate(session_id: str) -> Optional[str]:
    try:
        req = urllib.request.Request(
            ICA_USER_INFO_URL,
            headers={"Cookie": f"thSessionId={session_id}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
    except urllib.error.URLError as e:
        print(f"ERROR: Could not reach ICA — {e}")
        return None
    token = data.get("accessToken")
    if not token:
        print("ERROR: ICA authentication failed (expired session?)")
        print("  Update TH_SESSION_ID in .env and try again.")
        return None
    print(f"ICA: Authenticated as {data.get('firstName', '?')}")
    return token


def ica_get_lists(token: str, session_id: str) -> list[dict]:
    req = urllib.request.Request(
        f"{ICA_API_BASE}/list/all",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Cookie": f"thSessionId={session_id}",
        },
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    if isinstance(data, dict) and "items" in data:
        return data["items"]
    return data if isinstance(data, list) else []


def ica_add_item(token: str, list_id: str, text: str) -> bool:
    payload = json.dumps({
        "isStriked": False, "quantity": {}, "text": text, "article": None,
    }).encode()
    req = urllib.request.Request(
        f"{ICA_API_BASE}/list/{list_id}/row",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "*/*",
            "Origin": "https://www.ica.se",
            "Referer": "https://www.ica.se/",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        print(f"  FAILED ({e.code}): {text}")
        return False


def ica_delete_rows(token: str, list_id: str, row_ids: list) -> bool:
    """Bulk-delete rows from an ICA list."""
    payload = json.dumps(row_ids).encode()
    req = urllib.request.Request(
        f"{ICA_API_BASE}/list/{list_id}/rows",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "*/*",
        },
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status in (200, 204)
    except urllib.error.HTTPError as e:
        print(f"  DELETE FAILED ({e.code})")
        return False


def ica_create_list(token: str, name: str) -> Optional[str]:
    """Create a new ICA shopping list. Returns the list ID or None."""
    payload = json.dumps({"name": name}).encode()
    req = urllib.request.Request(
        f"{ICA_API_BASE}/list",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
            list_id = data.get("id")
            if list_id:
                print(f"  Created list: {name}")
                return list_id
            print("ERROR: No list ID in response")
            return None
    except urllib.error.HTTPError as e:
        print(f"ERROR: Could not create list ({e.code})")
        return None


def ica_clear_list(token: str, session_id: str, list_id: str, only_checked: bool):
    """Remove items from an ICA list. If only_checked, only remove striked (bought) items."""
    lists = ica_get_lists(token, session_id)
    target = next((l for l in lists if l["id"] == list_id), None)
    if not target:
        print("ERROR: Could not find list to clear")
        return

    rows = target.get("rows", [])
    to_delete = [r for r in rows if not only_checked or r.get("isStriked", False)]

    if not to_delete:
        print("  Nothing to clear.")
        return

    label = "checked" if only_checked else "all"
    print(f"  Clearing {len(to_delete)} {label} items...")
    row_ids = [row["id"] for row in to_delete]
    ica_delete_rows(token, list_id, row_ids)
    print(f"  Cleared.")


def parse_shopping_list_md() -> list[str]:
    """Parse inkopslista.md into ICA-formatted item strings."""
    items = []
    with open(SHOPPING_LIST_MD) as f:
        for line in f:
            m = re.match(r"^- (.+)$", line.strip())
            if not m:
                continue
            raw = m.group(1)
            if " — " in raw:
                name, qty = raw.split(" — ", 1)
                items.append(f"{qty} {name}")
            else:
                items.append(raw)
    return items


def ica_pick_list(token: str, session_id: str) -> Optional[tuple[str, str]]:
    """Let user pick an ICA list or create a new one. Returns (list_id, list_name) or None."""
    from datetime import date
    lists = ica_get_lists(token, session_id)

    print("\nYour ICA lists:")
    for i, lst in enumerate(lists, 1):
        n_rows = len(lst.get("rows", []))
        print(f"  {i}. {lst.get('name', '?')} ({n_rows} items)")
    print(f"  N. Create new list")

    choice = input(f"Which list? [1-{len(lists)}] or [N]ew: ").strip().lower()
    if choice == "n":
        default_name = date.today().strftime("%Y-%m-%d")
        name = input(f"List name [{default_name}]: ").strip() or default_name
        list_id = ica_create_list(token, name)
        if not list_id:
            return None
        return list_id, name

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(lists):
            return lists[idx]["id"], lists[idx].get("name", "?")
    except ValueError:
        pass
    print("Invalid choice.")
    return None


def push_to_ica(env: dict):
    """Parse inkopslista.md and push items to ICA."""
    session_id = env.get("TH_SESSION_ID")
    if not session_id:
        print("ERROR: TH_SESSION_ID not set in .env")
        sys.exit(1)

    if not os.path.exists(SHOPPING_LIST_MD):
        print("ERROR: No inkopslista.md found. Save from the checklist first.")
        sys.exit(1)

    items = parse_shopping_list_md()
    if not items:
        print("No items in inkopslista.md")
        return

    token = ica_authenticate(session_id)
    if not token:
        sys.exit(1)
    pick = ica_pick_list(token, session_id)
    if not pick:
        sys.exit(1)
    ica_push_items(token, session_id, pick[0], pick[1], items)


def ica_push_items(token: str, session_id: str, list_id: str, list_name: str, items: list[str]):
    """Clear (optionally) and push items to an ICA list."""
    print(f"\nList: {list_name}")
    print(f"  {len(items)} items to push\n")

    lists = ica_get_lists(token, session_id)
    target = next((l for l in lists if l["id"] == list_id), None)
    n_existing = len(target.get("rows", [])) if target else 0

    if n_existing > 0:
        clear = input(f"Clear list first? ({n_existing} existing items) [a]ll / [c]hecked / [N]o: ").strip().lower()
        if clear in ("a", "all"):
            ica_clear_list(token, session_id, list_id, only_checked=False)
        elif clear in ("c", "checked"):
            ica_clear_list(token, session_id, list_id, only_checked=True)

    print(f"\nPushing {len(items)} items:\n")
    ok, fail = 0, 0
    for item in items:
        if ica_add_item(token, list_id, item):
            print(f"  + {item}")
            ok += 1
        else:
            fail += 1
        time.sleep(0.2)

    print(f"\nDone! {ok} added, {fail} failed.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    env = load_env()

    if len(sys.argv) > 1 and sys.argv[1] == "push":
        push_to_ica(env)
        return

    api_key = env.get("OPEN_AI_KEY") or env.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPEN_AI_KEY not set in .env")
        sys.exit(1)

    # Step 1: Read recipes from Mela
    print("Reading recipes from Mela...")
    recipes = read_mela_recipes()
    if not recipes:
        print("No recipes marked 'Want to Cook' in Mela.")
        sys.exit(0)

    print(f"\nFound {len(recipes)} recipes:")
    for r in recipes:
        n_ing = len(r["ingredients"])
        print(f"  - {r['title']} ({n_ing} ingredients)")

    # Step 2: Collect unique ingredient names and translate
    all_names = []
    for r in recipes:
        for ing in r["ingredients"]:
            if ing["name"] and ing["name"] not in all_names:
                all_names.append(ing["name"])

    print(f"\n{len(all_names)} unique ingredient names to translate.")
    translations = translate_ingredients(all_names, api_key)

    # Step 3: Consolidate
    recipe_titles, sections = consolidate(recipes, translations)
    total_items = sum(len(s["items"]) for s in sections)
    print(f"Consolidated into {total_items} unique items.")

    # Step 4: Serve interactive checklist
    serve_checklist(recipe_titles, sections)

    if not os.path.exists(SHOPPING_LIST_MD):
        print("\nNo list saved. Run 'weekly-shopping' again to retry.")
        return

    items = parse_shopping_list_md()
    print(f"\nShopping list saved: {len(items)} items in inkopslista.md")

    push = input("\nPush to ICA? [y/N] ").strip().lower()
    if push != "y":
        print("Done! Run 'weekly-shopping push' later to push to ICA.")
        return

    session_id = env.get("TH_SESSION_ID")
    if not session_id:
        print("ERROR: TH_SESSION_ID not set in .env — can't push to ICA.")
        return

    token = ica_authenticate(session_id)
    if not token:
        print("Run 'weekly-shopping push' to retry after fixing credentials.")
        return
    pick = ica_pick_list(token, session_id)
    if not pick:
        print("Run 'weekly-shopping push' to retry.")
        return
    ica_push_items(token, session_id, pick[0], pick[1], items)


if __name__ == "__main__":
    main()
