/** Generate a short unique id for canvas nodes/edges. */
export function uid(prefix = "n"): string {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}
