# kaleai-src patches

The recovered Kale.ai app's source lives on the VM at `~/kale/kaleai-src` (it is
not part of this repo). These files are the Forge additions, versioned here and
copied into place before rebuilding the `kaleai` image:

| file | destination in kaleai-src |
|---|---|
| `forge_routes.py` | `services/analysis/app/api/forge_routes.py` |
| `db_forge_design.py` | appended class → `services/analysis/app/models/db.py` |
| (router registration) | two lines in `services/analysis/app/main.py` |

Apply + rebuild:

```bash
scp forge_routes.py ubuntu@VM:~/kale/kaleai-src/services/analysis/app/api/
# append the ForgeDesign class to models/db.py, register the router in main.py
cd ~/kale && sudo docker compose --profile app build kaleai && sudo docker compose --profile app up -d kaleai
```

The `forge_designs` table is created by the app's own `Base.metadata.create_all`
at startup — additive, never altering existing tables. Rolling back the app
leaves the table in place, unused. Back up `/data/kale/kale.db` (sqlite backup
API, not a file copy — WAL) before any deploy that touches the schema.
