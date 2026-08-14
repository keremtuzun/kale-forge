from __future__ import annotations

from app.services.onshape import OnshapeConnection


class _Response:
    def raise_for_status(self):
        return self

    def json(self):
        return {}


class _Client:
    def __init__(self, calls, **_kwargs):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def post(self, url, json):
        self.calls.append((url, json))
        return _Response()


def test_parametric_revision_updates_one_editable_source(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.services.onshape.httpx.Client",
        lambda **kwargs: _Client(calls, **kwargs),
    )
    connection = OnshapeConnection()
    connection._credentials["user"] = ("access", "secret", "https://cad.onshape.com", "owner")

    result = connection.publish_parametric(
        "user", "Robot r2", "FeatureScript 3029;",
        document_id="doc", workspace_id="workspace", element_id="source-tab",
    )

    assert len(calls) == 1
    assert calls[0][0].endswith("/featurestudios/d/doc/w/workspace/e/source-tab/content")
    assert result["element_id"] == "source-tab"
    assert result["editable"] is True
    assert result["flattened"] is False
    assert result["mode"] == "featurescript-updated"
