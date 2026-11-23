import type { Metadata } from "next";

import BulkCreatePage from "./components/BulkCreatePage";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Bulk Create | Presenton",
  description: "Create many decks from one blueprint with the same quality as a single deck.",
};

export default function Page() {
  return <BulkCreatePage />;
}
