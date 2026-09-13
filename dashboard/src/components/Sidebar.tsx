"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Camera, History, LayoutDashboard, Settings, ShieldCheck } from "lucide-react";

const links = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/cameras", label: "Cameras", icon: Camera },
  { href: "/history", label: "Incidents", icon: History },
  { href: "/settings", label: "Settings", icon: Settings },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-64 glass-panel border-l-0 rounded-l-none min-h-screen flex flex-col p-4 max-[900px]:w-full max-[900px]:min-h-0 max-[900px]:rounded-none">
      <div className="flex items-center gap-3 mb-10 px-2 mt-4 max-[900px]:mb-4">
        <div className="w-9 h-9 rounded-xl bg-brand/20 border border-brand/30 flex items-center justify-center">
          <ShieldCheck className="w-5 h-5 text-brand" />
        </div>
        <div>
          <h1 className="text-xl font-bold tracking-wider">Shop<span className="text-brand">Aware</span></h1>
          <div className="text-[10px] uppercase tracking-[.18em] text-foreground/45">Retail AI</div>
        </div>
      </div>

      <nav className="flex-1 space-y-2 max-[900px]:flex max-[900px]:gap-2 max-[900px]:space-y-0 max-[900px]:overflow-x-auto">
        {links.map((link) => {
          const Icon = link.icon;
          const active = pathname === link.href;
          return (
            <Link
              key={link.href}
              href={link.href}
              className={`flex items-center gap-3 px-4 py-3 rounded-lg transition-all whitespace-nowrap ${
                active
                  ? "bg-brand/20 text-brand font-medium border border-brand/30"
                  : "text-foreground/70 hover:bg-white/5 hover:text-foreground border border-transparent"
              }`}
            >
              <Icon className="w-5 h-5" />
              {link.label}
            </Link>
          );
        })}
      </nav>

      <div className="mt-auto pt-8 pb-4 max-[900px]:hidden">
        <div className="px-4 py-3 rounded-lg bg-black/20 border border-glass-border">
          <div className="text-xs text-foreground/50 uppercase tracking-wider mb-1">Mode</div>
          <div className="flex items-center gap-2 text-sm text-warning">
            <span className="w-2 h-2 rounded-full bg-warning"></span>
            Development / Review
          </div>
        </div>
      </div>
    </aside>
  );
}
