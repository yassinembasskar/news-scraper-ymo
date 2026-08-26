import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/api/client";

export function useArchivedArticles() {
  return useQuery({
    queryKey: ["archived-articles"],
    queryFn: api.getArchivedArticles,
  });
}

function useInvalidateArchived() {
  const queryClient = useQueryClient();
  return () => {
    queryClient.invalidateQueries({ queryKey: ["archived-articles"] });
    queryClient.invalidateQueries({ queryKey: ["articles"] });
    queryClient.invalidateQueries({ queryKey: ["pipeline-stats"] });
  };
}

export function useDisarchiveArticle() {
  const invalidate = useInvalidateArchived();
  return useMutation({
    mutationFn: (articleId: string) => api.disarchiveArticle(articleId),
    onSuccess: invalidate,
  });
}

export function useFlushArchivedArticles() {
  const invalidate = useInvalidateArchived();
  return useMutation({
    mutationFn: api.flushArchivedArticles,
    onSuccess: invalidate,
  });
}
