import type { Metadata, Viewport } from "next";
import { Outfit } from "next/font/google";
import { RegisterSW } from "@/components/register-sw";
import "./globals.css";

const outfit = Outfit({
  subsets: ["latin"],
  variable: "--font-outfit",
  display: "swap"
});

export const metadata: Metadata = {
  title: "Z CLIPS",
  description: "AI-powered clip creation and management",
  applicationName: "Z CLIPS",
  appleWebApp: { capable: true, title: "Z CLIPS", statusBarStyle: "black-translucent" },
  manifest: "/manifest.webmanifest"
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#100b19"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={outfit.className}>
        <RegisterSW />
        {children}
      </body>
    </html>
  );
}
