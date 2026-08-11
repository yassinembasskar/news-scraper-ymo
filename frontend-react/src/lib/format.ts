/** "1d 14h 1min" / "10h 15min" / "20min" — mirrors the backend Streamlit
 * app's _format_duration(): drops any zero-valued unit entirely rather
 * than showing e.g. "0d 10h 0min". */
export function formatDuration(totalMinutes: number): string {
  const wholeMinutes = Math.round(Math.max(totalMinutes, 0));
  const days = Math.floor(wholeMinutes / (24 * 60));
  const hours = Math.floor((wholeMinutes % (24 * 60)) / 60);
  const minutes = wholeMinutes % 60;

  const parts: string[] = [];
  if (days) parts.push(`${days}d`);
  if (hours) parts.push(`${hours}h`);
  if (minutes || parts.length === 0) parts.push(`${minutes}min`);
  return parts.join(" ");
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "Never";
  return iso.replace("T", " ").slice(0, 19);
}

/** Falls back to the article's URL domain when source_name is missing —
 * happens for every Currents-API article, since that source's response has
 * no publisher field at all (confirmed against a real response). Not a
 * real publisher name, just a better-than-blank stand-in ("bbc.co.uk"
 * instead of "Unknown source"). Strips a leading "www." for readability. */
export function displaySourceName(sourceName: string | null | undefined, url: string): string {
  if (sourceName) return sourceName;
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "Unknown source";
  }
}

export function formatRelativeOrDate(iso: string | null | undefined): string {
  if (!iso) return "Never";
  const date = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMin = Math.floor(diffMs / 60000);

  if (diffMin < 1) return "Just now";
  if (diffMin < 60) return `${diffMin}min ago`;
  const diffHours = Math.floor(diffMin / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  return formatDateTime(iso);
}
