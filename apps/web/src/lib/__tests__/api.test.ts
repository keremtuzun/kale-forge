import { describe, it, expect, vi, beforeEach } from "vitest";
import { api } from "@/lib/api";

describe("api client", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      status: 200,
      headers: new Headers({ "content-type": "application/json" }),
      json: async () => [{ id: "p1", name: "demo" }],
    })));
  });

  it("builds the projects URL", async () => {
    await api.listProjects();
    const url = (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls[0][0];
    expect(String(url)).toContain("/api/projects");
  });

  it("posts create-project body", async () => {
    (fetch as unknown as { mockResolvedValue: (v: unknown) => void }).mockResolvedValue?.({
      ok: true, status: 200, headers: new Headers({ "content-type": "application/json" }), json: async () => ({}),
    });
    await api.createProject("board", "desc");
    const init = (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(String(init.body)).toContain("board");
  });
});
