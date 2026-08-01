import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: { me: vi.fn().mockRejectedValue(new Error("no session")) },
}));

import Home from "@/app/page";
import { AuthProvider, StudioGate } from "@/components/auth";

describe("welcome page", () => {
  it("leads with the studio and makes the parametric claim", () => {
    render(<Home />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toMatch(/Robots.*resolved/);
    // The only destination the site offers is the Design Studio.
    const links = screen.getAllByRole("link").filter((link) => link.getAttribute("href") === "/design");
    expect(links.length).toBeGreaterThan(0);
    for (const link of links) expect(link.getAttribute("href")).toBe("/design");
    // The Onshape promise the product rests on.
    expect(screen.getByText(/Nothing arrives flattened/)).toBeTruthy();
  });
});

describe("studio gate", () => {
  it("offers inline sign-in rather than redirecting to a login page", async () => {
    render(<AuthProvider><StudioGate><div>studio-body</div></StudioGate></AuthProvider>);
    const signIn = await screen.findAllByRole("button", { name: /sign in/i });
    expect(signIn.length).toBeGreaterThan(0);
    expect(screen.queryByText("studio-body")).toBeNull();
  });
});
