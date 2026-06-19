import { inject, Injectable, computed, signal } from '@angular/core';
import { ConfigService } from '../services/config.service';

export interface TokenRefreshResponse {
  access_token: string;
  refresh_token?: string;
  id_token?: string;
  token_type: string;
  expires_in: number;
  scope?: string;
}

interface LocalLoginApiResponse {
  access_token: string;
  token_type: string;
}

@Injectable({
  providedIn: 'root'
})
export class AuthService {
  private config = inject(ConfigService);
  private readonly tokenKey = 'access_token';
  private readonly idTokenKey = 'id_token';
  private readonly refreshTokenKey = 'refresh_token';
  private readonly tokenExpiryKey = 'token_expiry';
  private readonly stateKey = 'auth_state';
  private readonly codeVerifierKey = 'auth_code_verifier';
  private readonly returnUrlKey = 'auth_return_url';
  private readonly providerIdKey = 'auth_provider_id';

  // OIDC endpoints from runtime config
  private readonly oidcAuthorizationUrl = computed(() => this.config.oidcAuthorizationUrl());
  private readonly oidcTokenUrl = computed(() => this.config.oidcTokenUrl());
  private readonly oidcClientId = computed(() => this.config.oidcClientId());
  private readonly oidcScopes = computed(() => this.config.oidcScopes() || 'openid profile email');

  /** True when OIDC SSO is configured (oidcClientId is set in runtime config). */
  readonly isOidcConfigured = computed(() => !!this.config.oidcClientId());

  /** True when local username/password auth is enabled. */
  readonly isLocalAuthEnabled = computed(() => this.config.localAuthEnabled());

  private get redirectUri(): string {
    return `${window.location.origin}/auth/callback`;
  }

  /** Signal tracking the current authentication provider ID. */
  readonly currentProviderId = signal<string | null>(null);

  constructor() {
    this.updateProviderIdFromStorage();
  }

  /**
   * Get the current access token from localStorage.
   */
  getAccessToken(): string | null {
    return localStorage.getItem(this.tokenKey);
  }

  /**
   * Get the refresh token from localStorage.
   */
  getRefreshToken(): string | null {
    return localStorage.getItem(this.refreshTokenKey);
  }

  /**
   * Check if the current access token is expired or will expire soon.
   * @param bufferSeconds Buffer time in seconds before expiry to consider token expired (default: 60)
   */
  isTokenExpired(bufferSeconds: number = 60): boolean {
    const expiryStr = localStorage.getItem(this.tokenExpiryKey);
    if (!expiryStr) {
      return true;
    }

    const expiryTime = parseInt(expiryStr, 10);
    const currentTime = Date.now();
    const bufferTime = bufferSeconds * 1000;

    return currentTime >= (expiryTime - bufferTime);
  }

  /**
   * Check if user is authenticated (has a valid token).
   */
  isAuthenticated(): boolean {
    const token = this.getAccessToken();
    if (!token) {
      return false;
    }
    return !this.isTokenExpired();
  }

  // ─── PKCE Helpers ───────────────────────────────────────────────────

  /**
   * Generate a cryptographically random code verifier (43-128 chars) for PKCE.
   */
  private generateCodeVerifier(): string {
    const array = new Uint8Array(32);
    crypto.getRandomValues(array);
    return this.base64UrlEncode(array);
  }

  /**
   * Generate a SHA-256 code challenge from the code verifier for PKCE.
   */
  private async generateCodeChallenge(verifier: string): Promise<string> {
    const encoder = new TextEncoder();
    const data = encoder.encode(verifier);
    const digest = await crypto.subtle.digest('SHA-256', data);
    return this.base64UrlEncode(new Uint8Array(digest));
  }

  /**
   * Generate a random state string for CSRF protection.
   */
  private generateRandomState(): string {
    const array = new Uint8Array(32);
    crypto.getRandomValues(array);
    return this.base64UrlEncode(array);
  }

  /**
   * Base64url encode a Uint8Array (no padding, URL-safe).
   */
  private base64UrlEncode(buffer: Uint8Array): string {
    let binary = '';
    for (let i = 0; i < buffer.length; i++) {
      binary += String.fromCharCode(buffer[i]);
    }
    return btoa(binary)
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=+$/, '');
  }

  // ─── OIDC Login ──────────────────────────────────────────────────────

  /**
   * Initiates the OIDC OAuth 2.0 authorization code flow with PKCE.
   * Redirects to the configured OIDC provider (e.g. Azure Entra).
   *
   * @param providerId Optional identity provider hint passed as `identity_provider`
   */
  async login(providerId?: string): Promise<void> {
    const authUrl = this.oidcAuthorizationUrl();
    if (!authUrl) {
      throw new Error('OIDC is not configured. Use local login instead.');
    }

    const state = this.generateRandomState();
    const codeVerifier = this.generateCodeVerifier();
    const codeChallenge = await this.generateCodeChallenge(codeVerifier);

    sessionStorage.setItem(this.stateKey, state);
    sessionStorage.setItem(this.codeVerifierKey, codeVerifier);

    if (providerId) {
      localStorage.setItem(this.providerIdKey, providerId);
    }

    const params = new URLSearchParams({
      response_type: 'code',
      client_id: this.oidcClientId(),
      redirect_uri: this.redirectUri,
      scope: this.oidcScopes(),
      state,
      code_challenge: codeChallenge,
      code_challenge_method: 'S256',
    });

    if (providerId) {
      params.set('identity_provider', providerId);
    }

    window.location.href = `${authUrl}?${params}`;
  }

  // ─── Local Login ──────────────────────────────────────────────────────

  /**
   * Authenticates using local username/password via POST /auth/local/login.
   * Stores the returned token exactly like an OIDC token (same storage keys).
   * Local tokens expire in 8 hours (backend default).
   */
  async localLogin(email: string, password: string): Promise<void> {
    const appApiUrl = this.config.appApiUrl();
    const response = await fetch(`${appApiUrl}/auth/local/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });

    if (!response.ok) {
      const errorBody = await response.json().catch(() => ({ detail: 'Login failed' }));
      throw new Error(errorBody.detail || 'Invalid email or password');
    }

    const data: LocalLoginApiResponse = await response.json();
    if (!data.access_token) {
      throw new Error('Invalid response from auth server');
    }

    this.storeTokens({ access_token: data.access_token, expires_in: 28800 });
  }

  // ─── Callback / Token Exchange ──────────────────────────────────────

  /**
   * Exchanges an OIDC authorization code for tokens via the configured token endpoint.
   */
  async handleCallback(code: string, state: string): Promise<void> {
    const storedState = sessionStorage.getItem(this.stateKey);
    if (state !== storedState) {
      this.clearStoredState();
      throw new Error('State mismatch. Security validation failed. Please try logging in again.');
    }

    const codeVerifier = sessionStorage.getItem(this.codeVerifierKey);
    if (!codeVerifier) {
      this.clearStoredState();
      throw new Error('No code verifier found. Please initiate login again.');
    }

    const tokenUrl = this.oidcTokenUrl();
    if (!tokenUrl) {
      this.clearStoredState();
      throw new Error('OIDC token endpoint not configured.');
    }

    const response = await fetch(tokenUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'authorization_code',
        client_id: this.oidcClientId(),
        code,
        redirect_uri: this.redirectUri,
        code_verifier: codeVerifier,
      }),
    });

    if (!response.ok) {
      this.clearStoredState();
      const errorBody = await response.text();
      throw new Error(`Token exchange failed: ${errorBody}`);
    }

    const tokens: TokenRefreshResponse = await response.json();

    if (!tokens || !tokens.access_token) {
      this.clearStoredState();
      throw new Error('Invalid token response from OIDC provider');
    }

    this.storeTokens(tokens);
    this.clearStoredState();
    sessionStorage.removeItem(this.codeVerifierKey);
  }

  // ─── Token Refresh ───────────────────────────────────────────────────

  /**
   * Refresh the access token using the refresh token via the OIDC token endpoint.
   * Local-auth tokens (HS256, no refresh token) cannot be refreshed this way;
   * the user will be prompted to re-authenticate when the token expires.
   */
  async refreshAccessToken(): Promise<TokenRefreshResponse> {
    const refreshToken = this.getRefreshToken();
    if (!refreshToken) {
      throw new Error('No refresh token available');
    }

    const tokenUrl = this.oidcTokenUrl();
    if (!tokenUrl) {
      throw new Error('OIDC token endpoint not configured. Re-authentication required.');
    }

    const response = await fetch(tokenUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'refresh_token',
        client_id: this.oidcClientId(),
        refresh_token: refreshToken,
      }),
    });

    if (!response.ok) {
      if (response.status === 400 || response.status === 401) {
        this.clearTokens();
      }
      const errorBody = await response.text();
      throw new Error(`Token refresh failed: ${errorBody}`);
    }

    const tokens: TokenRefreshResponse = await response.json();

    if (!tokens || !tokens.access_token) {
      throw new Error('Invalid token refresh response');
    }

    this.storeTokens(tokens);
    return tokens;
  }

  // ─── Token Storage ──────────────────────────────────────────────────

  /**
   * Store tokens in localStorage.
   */
  storeTokens(response: { access_token: string; refresh_token?: string; id_token?: string; expires_in: number }): void {
    localStorage.setItem(this.tokenKey, response.access_token);

    if (response.id_token) {
      localStorage.setItem(this.idTokenKey, response.id_token);
    }

    if (response.refresh_token) {
      localStorage.setItem(this.refreshTokenKey, response.refresh_token);
    }

    // Calculate and store token expiry timestamp
    const expiryTime = Date.now() + response.expires_in * 1000;
    localStorage.setItem(this.tokenExpiryKey, expiryTime.toString());

    // Update provider ID from localStorage (set during login)
    this.updateProviderIdFromStorage();

    // Dispatch custom event to notify UserService of token change in same tab
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('token-stored', {
        detail: { token: response.access_token }
      }));
    }
  }

  /**
   * Clear all authentication tokens from localStorage.
   */
  clearTokens(): void {
    localStorage.removeItem(this.tokenKey);
    localStorage.removeItem(this.idTokenKey);
    localStorage.removeItem(this.refreshTokenKey);
    localStorage.removeItem(this.tokenExpiryKey);
    localStorage.removeItem(this.providerIdKey);

    this.currentProviderId.set(null);

    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('token-cleared'));
    }
  }

  /**
   * Get the Authorization header value.
   */
  getAuthorizationHeader(): string | null {
    const token = this.getAccessToken();
    return token ? `Bearer ${token}` : null;
  }

  /**
   * Get the stored ID token. Contains user profile claims (email, name, groups).
   */
  getIdToken(): string | null {
    return localStorage.getItem(this.idTokenKey);
  }

  // ─── State / Return URL / Provider ID ────────────────────────────────

  /**
   * Update provider ID from localStorage.
   */
  private updateProviderIdFromStorage(): void {
    const storedProviderId = this.getStoredProviderId();
    this.currentProviderId.set(storedProviderId);
  }

  /**
   * Get the stored state token from sessionStorage.
   */
  getStoredState(): string | null {
    return sessionStorage.getItem(this.stateKey);
  }

  /**
   * Clear the stored state token from sessionStorage.
   */
  clearStoredState(): void {
    sessionStorage.removeItem(this.stateKey);
  }

  /**
   * Get the stored return URL from sessionStorage.
   */
  getStoredReturnUrl(): string | null {
    return sessionStorage.getItem(this.returnUrlKey);
  }

  /**
   * Clear the stored return URL from sessionStorage.
   */
  clearStoredReturnUrl(): void {
    sessionStorage.removeItem(this.returnUrlKey);
  }

  /**
   * Get the stored provider ID from localStorage.
   */
  getStoredProviderId(): string | null {
    return localStorage.getItem(this.providerIdKey);
  }

  /**
   * Get the current provider ID from the signal.
   */
  getProviderId(): string | null {
    return this.currentProviderId();
  }

  // ─── Ensure Authenticated ──────────────────────────────────────────

  /**
   * Ensures the user is authenticated before making an HTTP request.
   * Attempts to refresh the token if expired.
   */
  async ensureAuthenticated(): Promise<void> {
    if (this.isAuthenticated()) {
      return;
    }

    const token = this.getAccessToken();
    if (token && this.isTokenExpired()) {
      try {
        await this.refreshAccessToken();
        if (this.isAuthenticated()) {
          return;
        }
      } catch (error) {
        throw new Error('User is not authenticated. Please login again.');
      }
    }

    throw new Error('User is not authenticated. Please login.');
  }

  // ─── Logout ─────────────────────────────────────────────────────────

  /**
   * Clears local tokens and navigates to the home page.
   * For OIDC providers that require a server-side logout, configure the
   * post-logout redirect via the provider's app registration.
   */
  async logout(): Promise<void> {
    this.clearTokens();
    window.location.href = '/';
  }
}
