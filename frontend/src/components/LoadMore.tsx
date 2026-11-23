import type { UseInfiniteQueryResult } from "@tanstack/react-query";

import { Button } from "./ui";

export function LoadMore({ query }: { query: UseInfiniteQueryResult }) {
  if (!query.hasNextPage) return null;
  return (
    <div className="load-more">
      <Button
        variant="secondary"
        busy={query.isFetchingNextPage}
        onClick={() => {
          void query.fetchNextPage();
        }}
      >
        Carregar mais
      </Button>
    </div>
  );
}
