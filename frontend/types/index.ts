// ==========================================
// USER & AUTH
// ==========================================

export interface User {
  user_id: string;
  auth_id?: string;
  email: string;
  full_name_ar: string;
  full_name_en?: string;
  license_number: string;
  subscription_tier: "free" | "basic" | "pro";
  created_at: string;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  user: User;
}

export interface RegisterRequest {
  email: string;
  password: string;
  full_name_ar: string;
  license_number: string;
}

export interface RegisterResponse {
  user: User;
  verification_sent: boolean;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
}

export interface AuthTokens {
  access_token: string;
  refresh_token: string;
}

export interface AuthResponse {
  access_token: string;
  refresh_token: string;
  user: User;
}
