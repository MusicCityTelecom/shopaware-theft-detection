import type { Metadata } from "next";
import "./globals.css";
import Sidebar from "@/components/Sidebar";
import AuthGate from "@/components/AuthGate";


export const metadata: Metadata = {
  title: "ShopAware",
  description: "Retail theft detection and incident review",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="antialiased flex">
        <AuthGate><Sidebar />
        <main className="flex-1 p-6 min-h-screen overflow-y-auto">{children}</main></AuthGate>
      </body>
    </html>
  );
}
