"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useState } from "react";
import { Loader2, LogOut, UserRound } from "lucide-react";
import { api, type KaleUser } from "@/lib/api";
import { Button, Input } from "@/components/ui/primitives";

type AuthState = { user: KaleUser | null; loading: boolean; refresh: () => Promise<void>; setUser: (user: KaleUser | null) => void };
const AuthContext = createContext<AuthState>({ user: null, loading: true, refresh: async () => {}, setUser: () => {} });

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<KaleUser | null>(null); const [loading, setLoading] = useState(true);
  const refresh = async () => { try { setUser(await api.me()); } catch { setUser(null); } finally { setLoading(false); } };
  useEffect(() => { void refresh(); }, []);
  return <AuthContext.Provider value={{ user, loading, refresh, setUser }}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);

/** Gate the Design Studio behind an inline account panel.
 *
 * The site has exactly two pages — the welcome page and the studio — so there are no
 * separate login/signup routes to redirect to. Signing in happens here, in place, and the
 * studio appears the moment it succeeds.
 */
export function StudioGate({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <div className="grid min-h-[55vh] place-items-center"><div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>;
  if (!user) return <StudioAccess />;
  return <>{children}</>;
}

function StudioAccess() {
  const { setUser } = useAuth();
  const [mode, setMode] = useState<"signin" | "create">("signin");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const user = mode === "create"
        ? await api.register(name.trim(), email.trim(), password)
        : await api.login(email.trim(), password);
      setUser(user);
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't work — check the details and try again.");
    } finally { setBusy(false); }
  };

  return <div className="mx-auto grid w-full max-w-6xl gap-16 px-6 py-20 lg:grid-cols-[1fr_400px] lg:items-center">
    <div>
      <p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">Design Studio</p>
      <h1 className="mt-4 max-w-xl text-4xl font-semibold tracking-tight">Your studio, behind one door.</h1>
      <p className="mt-4 max-w-lg leading-7 text-muted-foreground">
        Designs, revisions, and Onshape publishing are tied to an account so your work is there
        when you come back. Sign in — or create an account in about ten seconds — and the studio
        opens right here.
      </p>
      <ul className="mt-8 space-y-3 text-sm text-muted-foreground">
        <li className="flex gap-3"><span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-primary" />Every design you generate is saved to your account with its full revision history.</li>
        <li className="flex gap-3"><span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-primary" />Onshape keys are held in server memory only, never written to disk.</li>
        <li className="flex gap-3"><span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-primary" />No mailing list, no third-party sign-in — an email is just your key back in.</li>
      </ul>
    </div>
    <form onSubmit={submit} className="rounded-lg border border-border bg-card p-6 shadow-sm">
      <div className="grid grid-cols-2 gap-1 rounded-md bg-muted p-1 text-sm font-medium">
        {(["signin", "create"] as const).map((value) => (
          <button key={value} type="button" onClick={() => { setMode(value); setError(""); }}
            className={`rounded px-3 py-2 transition ${mode === value ? "bg-card shadow-sm" : "text-muted-foreground hover:text-foreground"}`}>
            {value === "signin" ? "Sign in" : "Create account"}
          </button>
        ))}
      </div>
      <div className="mt-5 space-y-4">
        {mode === "create" && <div>
          <label htmlFor="studio-name" className="mb-1.5 block text-xs font-medium">Name</label>
          <Input id="studio-name" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" required />
        </div>}
        <div>
          <label htmlFor="studio-email" className="mb-1.5 block text-xs font-medium">Email</label>
          <Input id="studio-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required />
        </div>
        <div>
          <label htmlFor="studio-password" className="mb-1.5 block text-xs font-medium">Password</label>
          <Input id="studio-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
            autoComplete={mode === "create" ? "new-password" : "current-password"} required minLength={8} />
        </div>
        {error && <p className="rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-600 dark:text-red-300">{error}</p>}
        <Button className="w-full" disabled={busy || !email || password.length < 8 || (mode === "create" && !name.trim())}>
          {busy && <Loader2 className="h-4 w-4 animate-spin" />} {mode === "signin" ? "Sign in" : "Create account"}
        </Button>
        <p className="text-xs leading-5 text-muted-foreground">Sessions use secure, HTTP-only cookies. Your designs stay private to your account.</p>
      </div>
    </form>
  </div>;
}

export function AccountNav() {
  const { user, loading, setUser } = useAuth(); const router = useRouter();
  if (loading) return <span className="h-8 w-24 animate-pulse rounded-md bg-muted" />;
  if (!user) return <Link href="/design" className="rounded-md bg-primary px-3.5 py-2 text-sm font-semibold text-primary-foreground transition hover:opacity-90">Open Studio</Link>;
  return <div className="flex items-center gap-1.5">
    <span className="flex items-center gap-2 rounded-md border border-border px-3 py-1.5 text-sm"><UserRound className="h-4 w-4 text-muted-foreground" /><span className="hidden sm:inline">{user.name}</span></span>
    <Button variant="ghost" size="icon" title="Sign out" onClick={async () => { await api.logout(); setUser(null); router.push("/"); }}><LogOut className="h-4 w-4" /></Button>
  </div>;
}
