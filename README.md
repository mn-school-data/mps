# Minneapolis Public Schools Analysis

Source for a static website on Minneapolis Public Schools finances since FY2015,
built from the district's audited annual financial reports, budget books, state
and federal data.

- `mps/`: the analysis text and the cleaned data tables (CSV, with source URLs)
- `scripts/build_site.py`: builds the site into `_site/` (Python 3.9+, standard library only)
- `.github/workflows/site.yml`: builds and deploys to GitHub Pages; manual trigger only

Build and view locally:

    python3 scripts/build_site.py
    python3 -m http.server -d _site 8000

then open http://localhost:8000 (or open `_site/index.html` directly).

## License

Text, charts and compiled tables: CC BY 4.0. Credit "Minneapolis Public Schools
Analysis" with a link to https://mn-school-data.github.io/mps/. Scripts: GPL-3.0-or-later.
See LICENSE.
