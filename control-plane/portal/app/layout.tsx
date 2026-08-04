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

const title = "Ethereum Validator Platform";
const description =
  "Current EKS development environment status and project documentation.";
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
        width: 1731,
        height: 909,
        alt: "Ethereum Validator Platform status page",
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
