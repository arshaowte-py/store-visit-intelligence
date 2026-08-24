# Store Visit Intelligence

Issue tracking built from store visit reports across the Frido retail network.

## What's here

| Path | What it is |
|---|---|
| `index.html` | The dashboard — leadership view, issue register, pitch questions. All photos embedded, works offline. |
| `issues.html` | Index of every issue, linked. |
| `issues/<REF>.html` | One page per issue — detail, tags, status, cross-store context, and all its photos. |
| `photos/<id>.jpg` | Every photograph as its own file with a stable URL. |

## Current coverage

18 stores · 373 issues · 255 photographs · 32 recurring cross-store patterns.

Reported by Saiyed Abdal, Ganesh sir, Pratik Hapase, Aniruddha Bansod, Arsh Aowte,
Nishrit Pandita and the retail VM leadership group.

## Linking from Asana

Each Asana task points at its issue page:

```
https://storevisit-intelligence.netlify.app/issues/SKYC-18.html
```

Individual photos are addressable too:

```
https://storevisit-intelligence.netlify.app/photos/00000537.jpg
```

## Deploying

Static site, no build step. Netlify serves the repository root — see `netlify.toml`.
Connect the repo in Netlify → Site configuration → Build & deploy, publish directory `.`,
build command empty.

## Regenerating

The site is generated from `registry.json` (the master data file) by `build_site.py`,
kept in the working source bundle rather than in this repo.

## Access

This site contains store-level operational criticism, named staff observations and
internal photographs. It should not be publicly reachable.
