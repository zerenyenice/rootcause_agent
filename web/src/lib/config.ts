export const DEFAULT_API_URL = "http://localhost:2024";
export const DEFAULT_ASSISTANT_ID = "rootcause";

export const API_URL = process.env.NEXT_PUBLIC_LANGGRAPH_API_URL || DEFAULT_API_URL;
export const ASSISTANT_ID = process.env.NEXT_PUBLIC_ASSISTANT_ID || DEFAULT_ASSISTANT_ID;
