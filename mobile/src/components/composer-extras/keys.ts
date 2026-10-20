// TanStack Query keys for the composer extras (web use-templates / use-my-blogs).
export const templateKeys = {
  all: ["templates"] as const,
  list: (q: string) => [...templateKeys.all, "list", q] as const,
};

export const myBlogsKeys = {
  all: ["my-blogs"] as const,
  list: () => [...myBlogsKeys.all, "list"] as const,
};
