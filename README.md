# Weekly Grocery

Automates the weekly grocery workflow:

1. Reads recipes marked "Want to Cook" in [Mela](https://mela.recipes/)
2. Translates ingredients to Swedish via OpenAI
3. Opens an interactive checklist in the browser — tick off what you already have
4. Pushes the remaining items to your [ICA](https://www.ica.se/) shopping list

Built for macOS with a Mela + ICA setup. The ICA API integration works for any ICA account and is the interesting part if you want to adapt this.

## Requirements

- macOS with [Mela](https://mela.recipes/) installed
- An ICA account
- An OpenAI API key (for ingredient translation; cached after first run)
- Python 3.9+ (no external dependencies — pure stdlib)

## Setup

**1. Clone and configure**

```bash
git clone https://github.com/yourname/weekly-grocery
cd weekly-grocery
cp .env.example .env
```

Edit `.env` with your credentials (see below).

**2. Optional: install as a global command**

```bash
ln -sf "$(pwd)/weekly.py" ~/.local/bin/weekly-shopping
chmod +x weekly.py
```

Then run `weekly-shopping` from anywhere.

## Getting your credentials

### `OPEN_AI_KEY`

Create an API key at [platform.openai.com/api-keys](https://platform.openai.com/api-keys).

Ingredients are translated in batches and cached in `ingredient_cache.json` — OpenAI is only called for new ingredients.

### `TH_SESSION_ID`

This is ICA's session cookie, used to exchange for a Bearer token.

1. Log in at [ica.se](https://www.ica.se/) in your browser
2. Open DevTools → Application → Cookies → `https://www.ica.se`
3. Copy the value of the `thSessionId` cookie
4. Paste it into your `.env`

The session expires periodically — if you get an authentication error, just grab a fresh cookie value.

## Usage

```bash
# Full flow: read Mela → translate → checklist → push to ICA
weekly-shopping

# Push only (if you already saved a list from the checklist)
weekly-shopping push
```

### What the checklist does

- Tick off items you already have at home
- Adjust quantities inline if needed
- Add custom items (things not in any recipe)
- "Spara inköpslista" saves the remaining items and returns to the terminal

## ICA API

The ICA integration lives in `weekly.py` under the `# ICA push` section. Key endpoints:

| Action | Method | URL |
|--------|--------|-----|
| Exchange session cookie for Bearer token | GET | `https://www.ica.se/api/user/information` |
| List all shopping lists | GET | `{base}/list/all` |
| Add item to list | POST | `{base}/list/{id}/row` |
| Bulk delete items | DELETE | `{base}/list/{id}/rows` |
| Create new list | POST | `{base}/list` |

Where `{base}` is `https://apimgw-pub.ica.se/sverige/digx/shopping-list/v1/api`.

Authentication uses the session cookie to fetch a Bearer token via `ica_authenticate()`, then all subsequent calls use `Authorization: Bearer <token>`.

## Project structure

```
weekly.py            # Main script
ingredient_cache.json  # Auto-generated translation cache (gitignored)
inkopslista.md         # Auto-generated shopping list output (gitignored)
.env                   # Your credentials (gitignored)
.env.example           # Template
```
