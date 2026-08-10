"""Kale Forge studio design store: server-side revision chains with ownership.

Lives beside the recovered app's own /api/designs (which keeps its namespace);
the Forge studio's Vercel function and frontend use /api/forge/designs. Each
design is a prompt CHAIN — the base prompt plus every accepted revision — so the
client never re-sends history and the chain cannot grow without bound: the store
caps steps and characters, and appends demand the revision the caller last saw
(optimistic concurrency).

Deployed into kaleai-src/services/analysis/app/api/; registered in main.py.
The table is created by Base.metadata.create_all at startup — additive only, so
rolling the app back simply leaves the table unused.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.db import ForgeDesign, User
from app.services.security import get_current_user, get_db

router = APIRouter(prefix="/api/forge/designs", tags=["forge-studio"])

MAX_STEPS = 30
MAX_CHAIN_CHARS = 6000


class CreateBody(BaseModel):
    prompt: str = Field(min_length=2, max_length=4000)
    season: str = Field(default="", max_length=64)
    name: str = Field(default="", max_length=200)


class ReviseBody(BaseModel):
    revision: str = Field(min_length=2, max_length=2000)
    expected_revision: int = Field(ge=1)


class RestoreBody(BaseModel):
    revision: int = Field(ge=1)


class RenameBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _out(d: ForgeDesign, with_chain: bool = True) -> dict:
    out = {
        "id": d.id, "name": d.name, "season": d.season, "revision": d.revision,
        "created_at": d.created_at.isoformat(), "updated_at": d.updated_at.isoformat(),
    }
    if with_chain:
        out["chain"] = json.loads(d.prompt_chain or "[]")
    return out


def _mine(db: Session, design_id: str, user: User, write: bool) -> ForgeDesign:
    d = db.get(ForgeDesign, design_id)
    if d is None or (d.owner_id != user.id and (write or not user.is_admin)):
        # a design someone else owns answers exactly like one that does not exist
        raise HTTPException(404, "design not found")
    return d


@router.get("")
def list_designs(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.execute(select(ForgeDesign).where(ForgeDesign.owner_id == user.id)
                      .order_by(ForgeDesign.updated_at.desc()).limit(50)).scalars().all()
    return [_out(d, with_chain=False) for d in rows]


@router.post("", status_code=201)
def create_design(body: CreateBody, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    d = ForgeDesign(owner_id=user.id, name=body.name.strip(), season=body.season.strip(),
                    prompt_chain=json.dumps([body.prompt.strip()]), revision=1,
                    created_at=_now(), updated_at=_now())
    db.add(d)
    db.commit()
    return _out(d)


@router.get("/{design_id}")
def get_design(design_id: str, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    return _out(_mine(db, design_id, user, write=False))


@router.post("/{design_id}/revisions")
def append_revision(design_id: str, body: ReviseBody, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    d = _mine(db, design_id, user, write=True)
    if body.expected_revision != d.revision:
        raise HTTPException(409, f"design is at revision {d.revision}, not "
                                 f"{body.expected_revision}; reload before editing")
    chain = json.loads(d.prompt_chain or "[]")
    step = body.revision.strip()
    if len(chain) >= MAX_STEPS or sum(len(s) for s in chain) + len(step) > MAX_CHAIN_CHARS:
        raise HTTPException(422, "this design's revision history is full; start a new "
                                 "design from the current state")
    chain.append(step)
    d.prompt_chain = json.dumps(chain)
    d.revision = len(chain)
    d.updated_at = _now()
    db.commit()
    return _out(d)


@router.post("/{design_id}/restore")
def restore_revision(design_id: str, body: RestoreBody, db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    d = _mine(db, design_id, user, write=True)
    chain = json.loads(d.prompt_chain or "[]")
    if body.revision > len(chain):
        raise HTTPException(422, f"design has {len(chain)} revisions")
    d.prompt_chain = json.dumps(chain[:body.revision])
    d.revision = body.revision
    d.updated_at = _now()
    db.commit()
    return _out(d)


@router.patch("/{design_id}")
def rename_design(design_id: str, body: RenameBody, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    d = _mine(db, design_id, user, write=True)
    d.name = body.name.strip()
    d.updated_at = _now()
    db.commit()
    return _out(d, with_chain=False)


@router.delete("/{design_id}", status_code=204)
def delete_design(design_id: str, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    db.delete(_mine(db, design_id, user, write=True))
    db.commit()
