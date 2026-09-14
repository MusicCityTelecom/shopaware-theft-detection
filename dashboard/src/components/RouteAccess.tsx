"use client";
import { usePathname } from "next/navigation";
import { useSession } from "./AuthGate";

export default function RouteAccess({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const user = useSession();
  if (["/users", "/groups", "/settings", "/training"].includes(path) && user?.role !== "admin") {
    return <p className="glass-panel p-6">This page requires administrator access. Your cameras are available from Overview and Cameras.</p>;
  }
  return children;
}
