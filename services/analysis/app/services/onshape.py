"""Small Onshape API client used by the private Design Studio connection."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import httpx


class OnshapeConnection:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._credentials: dict[str, tuple[str, str, str, str]] = {}

    def connect(self, user_id: str, access_key: str, secret_key: str, base_url: str = "https://cad.onshape.com") -> dict[str, Any]:
        base_url = base_url.rstrip("/")
        with httpx.Client(auth=(access_key, secret_key), timeout=30) as client:
            response = client.get(f"{base_url}/api/users/sessioninfo")
            if response.status_code >= 400:
                raise ValueError(f"Onshape rejected the credentials ({response.status_code})")
            profile = response.json()
        with self._lock:
            self._credentials[user_id] = (access_key, secret_key, base_url, str(profile.get("id") or ""))
        return {"connected": True, "user": profile.get("name") or profile.get("email") or "Onshape user",
                "base_url": base_url, "storage": "memory only"}

    def disconnect(self, user_id: str) -> None:
        with self._lock: self._credentials.pop(user_id, None)

    def status(self, user_id: str) -> dict[str, Any]:
        return {"connected": user_id in self._credentials, "storage": "memory only"}

    def publish(self, user_id: str, name: str, file_path: Path, document_id: str = "", workspace_id: str = "") -> dict[str, Any]:
        creds = self._credentials.get(user_id)
        if creds is None: raise RuntimeError("Connect an Onshape account first")
        access, secret, base, _owner_id = creds
        with httpx.Client(auth=(access, secret), timeout=90) as client:
            if document_id and workspace_id:
                did, wid = document_id, workspace_id
            else:
                created = client.post(f"{base}/api/v10/documents", json={"name": name}).raise_for_status().json()
                did = created["id"]; wid = created["defaultWorkspace"]["id"]
            with file_path.open("rb") as fh:
                resp = client.post(f"{base}/api/v6/translations/d/{did}/w/{wid}",
                    data={"formatName":"OBJ","flattenAssemblies":"false","joinAdjacentSurfaces":"false","storeInDocument":"true"},
                    files={"file":(file_path.name,fh,"text/plain")})
            resp.raise_for_status(); translation = resp.json(); tid = translation.get("id") or translation.get("translationId")
            result: dict[str, Any] = translation
            if tid:
                for _ in range(30):
                    time.sleep(2); poll=client.get(f"{base}/api/v9/translations/{tid}"); poll.raise_for_status(); result=poll.json()
                    if result.get("requestState") in {"DONE","FAILED"}: break
            if result.get("requestState") == "FAILED": raise RuntimeError(result.get("failureReason") or "Onshape translation failed")
        return {"document_id":did,"workspace_id":wid,"url":f"{base}/documents/{did}/w/{wid}",
                "translation":result.get("requestState","submitted"), "mode":"updated" if document_id else "created"}

    def publish_parametric(self, user_id: str, name: str, source: str,
                           document_id: str = "", workspace_id: str = "") -> dict[str, Any]:
        """Publish the design as an editable Feature Studio rather than an imported mesh.

        The OBJ path (`publish`) round-trips the robot through triangles, and a triangle has no
        wall thickness, no tooth count and no centre distance — so what arrives in Onshape can
        be looked at and not edited. This uploads generated FeatureScript instead: every part
        is a feature, every dimension is a number in the source, and the frame parameters are
        exposed in the feature dialog.

        Onshape has no single "create a Feature Studio with these contents" call, so this is
        two: create the element, then write its contents.
        """
        creds = self._credentials.get(user_id)
        if creds is None:
            raise RuntimeError("Connect an Onshape account first")
        access, secret, base, _owner_id = creds
        with httpx.Client(auth=(access, secret), timeout=90) as client:
            if document_id and workspace_id:
                did, wid = document_id, workspace_id
            else:
                created = client.post(f"{base}/api/v10/documents",
                                      json={"name": name}).raise_for_status().json()
                did = created["id"]
                wid = created["defaultWorkspace"]["id"]

            studio = client.post(f"{base}/api/v10/featurestudios/d/{did}/w/{wid}",
                                 json={"name": f"{name} — Kale source"})
            studio.raise_for_status()
            eid = studio.json()["id"]

            contents = client.post(
                f"{base}/api/v10/featurestudios/d/{did}/w/{wid}/e/{eid}/content",
                json={"contents": source})
            contents.raise_for_status()

        return {"document_id": did, "workspace_id": wid, "element_id": eid,
                "url": f"{base}/documents/{did}/w/{wid}/e/{eid}",
                "translation": "DONE", "mode": "featurescript",
                "editable": True,
                "note": ("Published as a Feature Studio. Add the 'Kale FRC Robot' feature in a "
                         "Part Studio to build it; every dimension stays editable in the "
                         "source and in the feature dialog.")}

    def copy_public_workspace(self, user_id: str, name: str) -> dict[str, Any]:
        creds = self._credentials.get(user_id)
        if creds is None: raise RuntimeError("Connect an Onshape account first")
        access, secret, base, owner_id = creds
        source_did = "9b4847a461c2a185e6ff320a"; source_wid = "6df8cee50adeaba604998544"
        body = {"isPublic": True, "newName": name, "ownerId": owner_id}
        with httpx.Client(auth=(access, secret), timeout=180) as client:
            response = client.post(f"{base}/api/v10/documents/{source_did}/workspaces/{source_wid}/copy", json=body)
            if response.status_code >= 400:
                raise RuntimeError(f"Onshape could not copy the public workspace ({response.status_code}): {response.text[:300]}")
            data = response.json()
        did = data.get("newDocumentId") or data.get("documentId") or data.get("id")
        workspace = data.get("newWorkspaceId") or data.get("workspaceId") or data.get("defaultWorkspace") or {}
        wid = workspace.get("id") if isinstance(workspace, dict) else workspace
        if not did or not wid: raise RuntimeError("Onshape copied the workspace but returned an unfamiliar response")
        return {"document_id": did, "workspace_id": wid, "url": f"{base}/documents/{did}/w/{wid}",
                "mode": "exact-workspace-copy", "source_document_id": source_did, "source_workspace_id": source_wid}

    def import_obj(self, user_id: str, name: str, filename: str, payload: bytes) -> dict[str, Any]:
        creds = self._credentials.get(user_id)
        if creds is None: raise RuntimeError("Connect your Onshape account in Design Studio first")
        access, secret, base, _owner_id = creds
        with httpx.Client(auth=(access, secret), timeout=180) as client:
            created = client.post(f"{base}/api/v10/documents", json={"name": name})
            created.raise_for_status(); document = created.json()
            did = document["id"]; wid = document["defaultWorkspace"]["id"]
            response = client.post(
                f"{base}/api/v6/translations/d/{did}/w/{wid}",
                data={"formatName": "OBJ", "flattenAssemblies": "false", "joinAdjacentSurfaces": "false", "storeInDocument": "true"},
                files={"file": (filename, payload, "text/plain")},
            )
            response.raise_for_status(); translation = response.json()
            tid = translation.get("id") or translation.get("translationId")
            state = translation.get("requestState", "submitted")
            if tid:
                for _ in range(60):
                    time.sleep(2)
                    poll = client.get(f"{base}/api/v9/translations/{tid}"); poll.raise_for_status()
                    translation = poll.json(); state = translation.get("requestState", state)
                    if state in {"DONE", "FAILED"}: break
            if state == "FAILED": raise RuntimeError(translation.get("failureReason") or "Onshape OBJ import failed")
        return {"document_id": did, "workspace_id": wid, "url": f"{base}/documents/{did}/w/{wid}",
                "translation": state, "mode": "obj-mesh-import"}


onshape = OnshapeConnection()
