/**
 * User model representing decoded JWT token data.
 * Matches the backend User model structure.
 */
export interface User {
  email: string;
  user_id: string;
  firstName: string;
  lastName: string;
  fullName: string;
  roles: string[];
  picture?: string;
}

/**
 * Effective permissions resolved from the backend AppRole RBAC system.
 * Returned by GET /users/me/permissions.
 */
export interface UserPermissions {
  appRoles: string[];
  tools: string[];
  models: string[];
  quotaTier: string | null;
  resolvedAt: string;
}

/**
 * Decoded JWT payload structure.
 * Uses an index signature to support dynamic claim names
 * from any OIDC provider.
 */
export interface JWTPayload {
  [key: string]: any;
  email?: string;
  preferred_username?: string;
  name?: string;
  given_name?: string;
  family_name?: string;
  roles?: string[];
  picture?: string;
  exp?: number;
  iat?: number;
  aud?: string | string[];
  iss?: string;
  sub?: string;
}
