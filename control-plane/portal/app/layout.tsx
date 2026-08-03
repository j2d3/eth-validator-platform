import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { CANONICAL_URL } from "../lib/canonical-origin";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const title = "Validator Platform — Field Console";
const description =
  "The project home and operating index for a spec-built, GitOps-operated Ethereum validator platform lab.";
const origin = CANONICAL_URL;
const socialImage = new URL("/og.png", origin).toString();

export const metadata: Metadata = {
  metadataBase: origin,
  title,
  description,
  alternates: { canonical: origin },
  openGraph: {
    type: "website",
    url: origin,
    title,
    description,
    images: [
      {
        url: socialImage,
        width: 1734,
        height: 907,
        alt: "Validator Platform field console — ready is not authorized",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title,
    description,
    images: [socialImage],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={`${geistSans.variable} ${geistMono.variable}`}>
        {children}
      </body>
    </html>
  );
}
