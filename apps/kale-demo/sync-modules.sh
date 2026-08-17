#!/usr/bin/env bash
# Refresh the bundled synthesis modules from services/analysis (the source of truth).
# The demo is a self-contained copy so it can deploy as one Vercel Python function;
# re-run this whenever frc_parts / frc_robot_knowledge / robot_spec change.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
src="$here/../../services/analysis/app/services"
modules="frc_parts frc_cad frc_season frc_featurescript frc_robot_knowledge robot_spec
         geometry_validation geometry_repair electronics_detail edit_planner component_graph edit_explorer edit_iterator"
for m in $modules; do cp "$src/$m.py" "$here/app/services/$m.py"; done
echo "synced: $modules"
