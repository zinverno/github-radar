// Build-time only: never enabled by URL, local storage or an unavailable backend.
export const STATIC_PREVIEW = import.meta.env.MODE === "static-preview";
