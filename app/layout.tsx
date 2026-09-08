import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'DSpec AI',
  description: 'Local spec-first software delivery workspace'
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
