import { describe, it, expect } from 'vitest';
import { parseRolesFromToken } from './parse-roles';

describe('parseRolesFromToken', () => {
  // -- standard `roles` claim (highest priority) --

  it('should use roles array claim first', () => {
    const roles = parseRolesFromToken({ roles: ['admin', 'editor'] });
    expect(roles).toEqual(['admin', 'editor']);
  });

  it('should prefer roles over custom:roles', () => {
    const roles = parseRolesFromToken({
      roles: ['system_admin'],
      'custom:roles': 'other_role',
    });
    expect(roles).toEqual(['system_admin']);
  });

  it('should trim and filter empty entries in roles array', () => {
    const roles = parseRolesFromToken({ roles: ['  Admin  ', '', 'Staff'] });
    expect(roles).toEqual(['Admin', 'Staff']);
  });

  // -- custom:roles backward compat --

  it('should parse custom:roles JSON array string', () => {
    const roles = parseRolesFromToken({
      'custom:roles': '["DotNetDevelopers","All-Employees Entra Sync","Staff"]',
    });
    expect(roles).toEqual(['DotNetDevelopers', 'All-Employees Entra Sync', 'Staff']);
  });

  it('should parse custom:roles single-element JSON array', () => {
    const roles = parseRolesFromToken({ 'custom:roles': '["Admin"]' });
    expect(roles).toEqual(['Admin']);
  });

  it('should return empty array for empty custom:roles JSON array', () => {
    const roles = parseRolesFromToken({ 'custom:roles': '[]' });
    expect(roles).toEqual([]);
  });

  it('should trim whitespace in custom:roles JSON array elements', () => {
    const roles = parseRolesFromToken({
      'custom:roles': '["  Admin  ", " Staff "]',
    });
    expect(roles).toEqual(['Admin', 'Staff']);
  });

  it('should filter empty strings from custom:roles JSON array', () => {
    const roles = parseRolesFromToken({
      'custom:roles': '["Admin", "", "Staff"]',
    });
    expect(roles).toEqual(['Admin', 'Staff']);
  });

  it('should parse custom:roles comma-separated string', () => {
    const roles = parseRolesFromToken({ 'custom:roles': 'admin,editor' });
    expect(roles).toEqual(['admin', 'editor']);
  });

  it('should trim spaces in custom:roles comma-separated roles', () => {
    const roles = parseRolesFromToken({
      'custom:roles': ' admin , editor , viewer ',
    });
    expect(roles).toEqual(['admin', 'editor', 'viewer']);
  });

  it('should handle single custom:roles comma-separated role', () => {
    const roles = parseRolesFromToken({ 'custom:roles': 'admin' });
    expect(roles).toEqual(['admin']);
  });

  it('should handle custom:roles as an array directly', () => {
    const roles = parseRolesFromToken({ 'custom:roles': ['admin', 'editor'] });
    expect(roles).toEqual(['admin', 'editor']);
  });

  // -- no roles at all --

  it('should return empty array when no role claims present', () => {
    const roles = parseRolesFromToken({});
    expect(roles).toEqual([]);
  });

  it('should return empty array when custom:roles is null', () => {
    const roles = parseRolesFromToken({ 'custom:roles': null });
    expect(roles).toEqual([]);
  });

  it('should return empty array when custom:roles is empty string', () => {
    const roles = parseRolesFromToken({ 'custom:roles': '' });
    expect(roles).toEqual([]);
  });

  it('should return empty array when custom:roles is whitespace', () => {
    const roles = parseRolesFromToken({ 'custom:roles': '   ' });
    expect(roles).toEqual([]);
  });
});
