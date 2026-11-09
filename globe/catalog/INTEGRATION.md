# Globe Catalog staging contract

Status: **pending**. This file is an example for a later, separate edit on the Ubuntu machine. It was not applied.

## What was not available here

The catalog lives at `~/dev/geoknight-openatlas` on the user's Ubuntu computer and has historically had no git remote. This cloud environment cannot read that tree. The public page at `https://globes.geoknightgis.com/` asks for a sign-in and was not modified. `api/app/globes.py` and `caddy/Caddyfile` were not read and were not changed.

Do not deploy this globe, do not publish it, and do not change catalog access, plans, or admin controls from this branch.

## What to copy later

After review, the viewer can be run on loopback and linked from the catalog. Suggested pieces, all unconfirmed against `globes.py`:

| Piece | In this repo | Suggested catalog use |
| --- | --- | --- |
| Viewer | `uv run census-globe` on `127.0.0.1:8765` | Reverse-proxy a path such as `/florida-census/` |
| Card image | `globe/catalog/card-thumbnail.png` | Catalogue thumbnail |
| Jazzed card | `globe/catalog/card-jazz.png` | Feature card |
| Example metadata | `globe/catalog/card.example.json` | Starting point only. Field names are a proposal. |

Example Caddy snippet, **not installed**:

```
handle /florida-census/* {
    reverse_proxy 127.0.0.1:8765
}
```

The globe server refuses any bind other than `127.0.0.1`. Keep it that way unless a later reviewed change says otherwise.

## Card metadata

`card.example.json` is the proposed card. It includes the copyright line and the Census disclaimer. It does not include a phone number, a price, a client name, a certification, or a public repository link.

The images are rendered from the same county geometry and the same ACS export as the viewer. They are staging assets, not a live catalog update.

## Copyright

© GeoKnight GIS. All rights reserved.
