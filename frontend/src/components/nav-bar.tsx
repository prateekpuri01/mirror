"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_LINKS = [
  { href: "/", label: "Jobs" },
  { href: "/companies", label: "Companies" },
];

export function NavBar() {
  const pathname = usePathname();

  return (
    <nav className="border-b bg-background">
      <div className="max-w-[1400px] mx-auto px-6 flex items-center justify-between h-12">
        <Link href="/" className="text-sm font-bold tracking-tight">
          Job Board
        </Link>
        <div className="flex items-center gap-1">
          {NAV_LINKS.map((link) => {
            const isActive =
              link.href === "/"
                ? pathname === "/"
                : pathname.startsWith(link.href);
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`px-3 py-1.5 text-sm rounded-md transition-colors ${
                  isActive
                    ? "bg-muted font-medium text-foreground"
                    : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
                }`}
              >
                {link.label}
              </Link>
            );
          })}
        </div>
      </div>
    </nav>
  );
}
