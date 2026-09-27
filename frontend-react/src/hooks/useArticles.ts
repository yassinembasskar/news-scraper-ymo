import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/api/client";
import type { ArticleFilters } from "@/api/client";

export function useArticles(filters: ArticleFilters) {
  return useQuery({
    queryKey: ["articles", filters],
    queryFn: () => api.getArticles(filters),
  });
}

export function useArticle(articleId: string | undefined) {
  return useQuery({
    queryKey: ["article", articleId],
    queryFn: () => api.getArticle(articleId as string),
    enabled: !!articleId,
  });
}

export function useRetryExtraction() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (articleId: string) => api.retryArticleExtraction(articleId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["articles"] });
      queryClient.invalidateQueries({ queryKey: ["pipeline-stats"] });
    },
  });
}

export function useSubmitManualContent(articleId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (content: string) => api.submitManualContent(articleId, content),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["articles"] });
      queryClient.invalidateQueries({ queryKey: ["article", articleId] });
    },
  });
}
