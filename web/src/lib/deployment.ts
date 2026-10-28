import { createContext } from "react";

export const DeploymentContext = createContext<"local" | "public" | null>(null);
