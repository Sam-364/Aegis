import Link from "next/link";
import { EmptyState } from "@/components/ui/EmptyState";

export default function NotFound() {
  return (
    <div className="p-8">
      <EmptyState
        title="No such page"
        hint="The console has no route at this address."
        action={
          <Link href="/" className="micro rounded-xs border border-hairline-2 px-2 py-1 hover:border-muted hover:text-ink">
            Back to overview
          </Link>
        }
      />
    </div>
  );
}
