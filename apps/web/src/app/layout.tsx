import type { Metadata } from "next";
import Link from "next/link";
import { DISCLAIMER } from "@kale/shared-types";
import { ThemeProvider, ThemeToggle } from "@/components/theme";
import { AccountNav, AuthProvider } from "@/components/auth";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000"),
  title: "Kale Forge — Engineering, resolved.",
  description:
    "Describe a part, a mechanism, a board or a whole robot; get an engineered, fully editable design. Parametric CAD, real part numbers, and Onshape publishing from a self-hosted model.",
  icons: { icon: "/brand/kale-forge-mark.svg", apple: "/brand/kale-forge-mark.svg" },
  openGraph: {
    title: "Kale Forge — Engineering, resolved.",
    description: "Engineering-grounded design synthesis with editable parametric Onshape source.",
    images: [{ url: "/og.png", width: 1734, height: 907, alt: "Kale Forge parametric robot design" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "Kale Forge — Engineering, resolved.",
    description: "Engineering-grounded design synthesis with editable parametric Onshape source.",
    images: ["/og.png"],
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <ThemeProvider>
          <AuthProvider>
            <div className="flex min-h-screen flex-col">
              <header className="sticky top-0 z-10 border-b border-border bg-background/90 backdrop-blur">
                <div className="mx-auto flex h-16 w-full max-w-6xl items-center justify-between px-6">
                  <Link href="/" className="flex items-center gap-2.5 font-semibold">
                    <img src="/brand/kale-forge-mark.svg" alt="" className="h-7 w-7 object-contain" />
                    <span className="tracking-tight">Kale Forge</span>
                  </Link>
                  <nav className="flex items-center gap-1 text-sm">
                    <Link href="/" className="px-3 py-2 text-muted-foreground transition hover:text-foreground">Welcome</Link>
                    <Link href="/design" className="px-3 py-2 text-muted-foreground transition hover:text-foreground">Design Studio</Link>
                    <span className="mx-2 h-5 w-px bg-border" aria-hidden />
                    <ThemeToggle />
                    <AccountNav />
                  </nav>
                </div>
              </header>
              <main className="w-full flex-1">{children}</main>
              <footer className="border-t border-border">
                <div className="mx-auto flex w-full max-w-6xl flex-col gap-3 px-6 py-8 text-xs leading-5 text-muted-foreground md:flex-row md:items-start md:gap-6">
                  <img src="/brand/kale-forge-mark.svg" alt="Kale Forge" className="h-6 w-6 shrink-0 object-contain" />
                  <span className="max-w-4xl">{DISCLAIMER}</span>
                </div>
              </footer>
            </div>
          </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
