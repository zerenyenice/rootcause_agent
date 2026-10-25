export const DEFAULT_AGENT_URL = "http://localhost:8100";
export const DEFAULT_API_URL = "http://localhost:8000";

/** Agent service (threads, streaming runs, approvals). */
export const AGENT_URL = process.env.NEXT_PUBLIC_AGENT_URL || DEFAULT_AGENT_URL;
/** Backend API (connections, catalog). */
export const API_URL = process.env.NEXT_PUBLIC_API_URL || DEFAULT_API_URL;
