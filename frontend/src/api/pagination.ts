export interface PageResult<T> {
  items: T[];
  next_cursor: string | null;
  has_more: boolean;
}

export interface PageQuery {
  cursor?: string;
  limit?: number;
  search?: string;
  sort?: string;
  order?: "asc" | "desc";
  /** Comma-separated status filter (C6-1 cold/hot separation, executions list). */
  status?: string;
}

export function withQuery(
  path: string,
  values: object,
): string {
  const query = new URLSearchParams();
  const entries = Object.entries(values) as [
    string,
    string | number | null | undefined,
  ][];
  for (const [key, value] of entries) {
    if (value !== undefined && value !== null && value !== "") {
      query.set(key, String(value));
    }
  }
  const suffix = query.toString();
  return suffix ? `${path}?${suffix}` : path;
}
