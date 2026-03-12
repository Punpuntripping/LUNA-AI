import { createBrowserClient } from "@supabase/ssr";

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL!;
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!;

/**
 * Supabase browser client using @supabase/ssr.
 * Handles cookie-based session management automatically
 * (access token in memory, refresh token in HttpOnly cookie).
 */
export const supabase = createBrowserClient(supabaseUrl, supabaseAnonKey);
