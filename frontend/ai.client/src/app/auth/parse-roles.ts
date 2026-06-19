/**
 * Parse roles from a JWT token payload.
 *
 * Reads the standard `roles` OIDC claim (issued by Azure Entra and the local auth backend).
 *
 * @param payload Decoded JWT payload object
 * @returns Array of role strings
 */
export function parseRolesFromToken(payload: Record<string, unknown>): string[] {
  const rolesRaw = payload['roles'];
  if (Array.isArray(rolesRaw)) {
    return rolesRaw.map((r: unknown) => String(r).trim()).filter(Boolean);
  }
  return [];
}
