import { API_BASE_URL, TILE_API_BASE_URL } from '../config';
import { getSessionId } from './session';
import { ApiError } from './types';

type FetchTarget = 'app' | 'tile';

type ApiFetchOptions = Omit<RequestInit, 'headers'> & {
  headers?: HeadersInit;
  skipSessionHeader?: boolean;
};

const trimTrailingSlash = (value: string): string => value.replace(/\/+$/, '');

const joinUrl = (baseUrl: string, path: string): string => {
  const cleanBase = trimTrailingSlash(baseUrl);
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  return `${cleanBase}${normalizedPath}`;
};

const makeHeaders = (headers?: HeadersInit): Headers => {
  const h = new Headers(headers);
  const token = localStorage.getItem('auth_token');
  if (token) {
    h.set('Authorization', `Bearer ${token}`);
  }
  return h;
};

const baseUrlFor = (target: FetchTarget): string => {
  if (target === 'tile') {
    return TILE_API_BASE_URL || API_BASE_URL || '';
  }
  return API_BASE_URL || '';
};

const request = async (
  target: FetchTarget,
  path: string,
  options: ApiFetchOptions = {}
): Promise<Response> => {
  const { headers, skipSessionHeader = false, ...init } = options;
  const requestHeaders = makeHeaders(headers);

  if (!skipSessionHeader) {
    requestHeaders.set('X-Session-Id', getSessionId());
  }

  const baseUrl = baseUrlFor(target);
  const url = joinUrl(baseUrl, path);
  const response = await fetch(url, {
    ...init,
    headers: requestHeaders,
  });

  if (!response.ok) {
    throw await ApiError.fromResponse(response);
  }
  return response;
};

export const apiFetchApp = (path: string, options: ApiFetchOptions = {}): Promise<Response> =>
  request('app', path, options);

export const apiFetchTile = (path: string, options: ApiFetchOptions = {}): Promise<Response> =>
  request('tile', path, options);
