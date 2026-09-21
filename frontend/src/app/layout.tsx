import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { Toaster } from "@/components/ui/toaster";
import { ThemeProvider } from "@/components/theme-provider";
import { Providers } from "./providers";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "DAAS — AI Business Intelligence Operating System",
  description:
    "DAAS is an AI-native, multi-agent Business Intelligence Operating System. From raw data to analytics, forecasts, customer intelligence, and grounded reports you can trust.",
  keywords: [
    "DAAS",
    "Business Intelligence",
    "AI Agents",
    "Forecasting",
    "Churn Prediction",
    "Data Analytics",
    "Marketing Intelligence",
  ],
  authors: [{ name: "DAAS" }],
  icons: {
    icon: "/logo.svg",
  },
  openGraph: {
    title: "DAAS — AI Business Intelligence Operating System",
    description:
      "From raw data to analytics, forecasts, customer intelligence, and action — powered by specialized AI agents.",
    siteName: "DAAS",
    type: "website",
  },
  twitter: {
    card: "summary_large_image",
    title: "DAAS — AI BI Operating System",
    description:
      "From raw data to analytics, forecasts, customer intelligence, and action — powered by specialized AI agents.",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased bg-background text-foreground min-h-screen`}
      >
        <ThemeProvider
          attribute="class"
          defaultTheme="dark"
          enableSystem={false}
          disableTransitionOnChange
        >
          <Providers>
            {children}
            <Toaster />
          </Providers>
        </ThemeProvider>
      </body>
    </html>
  );
}
