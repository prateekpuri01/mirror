import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "./providers";
import { NavBar } from "@/components/nav-bar";

export const metadata: Metadata = {
  title: {
    default: "Mirror",
    template: "%s | Mirror",
  },
  description: "AI-powered job search assistant that reflects your best self at every opportunity",
  icons: {
    icon: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">
        <Providers>
          <NavBar />
          {children}
        </Providers>
      </body>
    </html>
  );
}
