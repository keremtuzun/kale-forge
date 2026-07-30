from __future__ import annotations

from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.services.design_studio import get_design_studio
from app.services.frc_season import SELECTABLE, season_options
from app.services.onshape import onshape
from app.services.security import get_current_user

router = APIRouter(prefix="/api/designs", tags=["design-studio"])

# "" means "work it out from the prompt"; anything else has to be a season Kale actually models.
SeasonKey = Literal["", "2026-rebuilt", "2025-reefscape", "offseason"]


class CreateDesign(BaseModel):
    kind: Literal["pcb", "robot"]
    prompt: str = Field(min_length=8, max_length=5000)
    name: str = Field(default="", max_length=72)
    season: SeasonKey = ""


class EditDesign(BaseModel):
    prompt: str = Field(min_length=3, max_length=3000)


class OnshapeCredentials(BaseModel):
    access_key: str = Field(min_length=4, max_length=256)
    secret_key: str = Field(min_length=4, max_length=256)
    base_url: str = "https://cad.onshape.com"


@router.get("")
def list_designs(user=Depends(get_current_user)):
    return get_design_studio().list(user.id, user.is_admin)


@router.get("/seasons")
def list_seasons():
    """The seasons the Design Studio can build for, for the selector in the UI.

    Public: it is the game manual restated, and the UI needs it before a user signs in.
    """
    return {"seasons": season_options(), "default": SELECTABLE[0] if SELECTABLE else ""}


@router.post("", status_code=201)
def create_design(body: CreateDesign, user=Depends(get_current_user)):
    return get_design_studio().create(body.kind, body.prompt, body.name, user.id, body.season)


@router.post("/example", status_code=201)
def load_worked_example(user=Depends(get_current_user)):
    """Create the worked example on demand. Workspaces start empty; this is the opt-in."""
    return get_design_studio().create_worked_example(user.id)


@router.get("/onshape/status")
def onshape_status(user=Depends(get_current_user)):
    return onshape.status(user.id)


@router.post("/onshape/connect")
def onshape_connect(body: OnshapeCredentials, user=Depends(get_current_user)):
    try: return onshape.connect(user.id, body.access_key, body.secret_key, body.base_url)
    except Exception as exc: raise HTTPException(400, str(exc)) from exc


@router.delete("/onshape/connect", status_code=204)
def onshape_disconnect(user=Depends(get_current_user)):
    onshape.disconnect(user.id)


class ExactCopyBody(BaseModel):
    name: str = Field(default="2025 Serpentheim — Editable Copy", min_length=3, max_length=120)


@router.post("/templates/serpentheim/copy", status_code=201)
def copy_serpentheim(body: ExactCopyBody, user=Depends(get_current_user)):
    try:
        copied = onshape.copy_public_workspace(user.id, body.name.strip())
        return get_design_studio().record_exact_copy(user.id, body.name.strip(), copied)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{design_id}")
def get_design(design_id: str, user=Depends(get_current_user)):
    try: return get_design_studio().get(design_id, user.id, user.is_admin)
    except FileNotFoundError as exc: raise HTTPException(404, "design not found") from exc


@router.post("/{design_id}/edit")
def edit_design(design_id: str, body: EditDesign, user=Depends(get_current_user)):
    try: return get_design_studio().edit(design_id, body.prompt, user.id)
    except FileNotFoundError as exc: raise HTTPException(404, "design not found") from exc


@router.delete("/{design_id}", status_code=204)
def delete_design(design_id: str, user=Depends(get_current_user)):
    try: get_design_studio().delete(design_id, user.id)
    except FileNotFoundError as exc: raise HTTPException(404, "design not found") from exc


@router.get("/{design_id}/files/{filename}")
def download_file(design_id: str, filename: str, user=Depends(get_current_user)):
    try: path=get_design_studio().artifact(design_id, filename, user.id, user.is_admin)
    except FileNotFoundError as exc: raise HTTPException(404, "artifact not found") from exc
    return FileResponse(path, filename=path.name)


@router.post("/{design_id}/onshape/publish")
def publish_onshape(design_id: str, mesh: bool = False, user=Depends(get_current_user)):
    """Publish to Onshape as an editable Feature Studio.

    `mesh=true` falls back to the old OBJ translation. It is kept because an imported mesh is
    occasionally what someone wants — a quick visual reference, or a shape to measure against —
    but it is no longer the default, because what it produces cannot be edited: a triangle
    carries no wall thickness, no tooth count and no centre distance, so every dimension the
    design was built from is gone by the time it lands.
    """
    studio = get_design_studio()
    try:
        design = studio.get(design_id, user.id)
        if design["kind"] != "robot":
            raise HTTPException(400, "Only robot assemblies publish to Onshape")
        prior = design.get("onshape") or {}
        title = f"{design['name']} — Kale r{design['revision']}"
        if mesh:
            obj = next(name for name in design["artifacts"] if name.endswith(".obj"))
            result = onshape.publish(user.id, title, studio.artifact(design_id, obj, user.id),
                                     prior.get("document_id", ""), prior.get("workspace_id", ""))
        else:
            source = studio.artifact(design_id, "KaleRobot.fs", user.id).read_text()
            result = onshape.publish_parametric(
                user.id, title, source,
                prior.get("document_id", ""), prior.get("workspace_id", ""))
        studio.set_onshape(design_id, user.id, result)
        return result
    except FileNotFoundError as exc: raise HTTPException(404, "design not found") from exc
    except (RuntimeError, StopIteration, httpx.HTTPError) as exc: raise HTTPException(400, str(exc)) from exc
