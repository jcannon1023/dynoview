# DynoView

League hub for The Shermonic Dynasty: team pages, contention windows, trade ideas and league history.

- `index.html` — the site (works on phones and desktop)
- `build_data.py` — pulls Sleeper, FantasyCalc and DynastyProcess and writes `data.json`
- `config.json` — league id, manager nicknames, age windows
- `.github/workflows/update-data.yml` — rebuilds `data.json` every 30 minutes

Data: [Sleeper](https://sleeper.com) · Values: [FantasyCalc](https://www.fantasycalc.com) and [DynastyProcess](https://dynastyprocess.com)

## Two leagues
- Shermonic: `config.json` -> `data.json`, served at `/dynoview/` (T-rex skin).
- FN: `fn/config.json` -> `fn/data.json`, served at `/dynoview/fn/` (triceratops skin). `fn/index.html` is generated from `index.html` by `make_pages.py`; edit `index.html`, never the copy.
- Draft order rule per league in its config: `"draft_order": "max_pf"` or `"standings"`; playoff teams always follow by playoff finish, champion last.
