export type ApiErrorBody = Record<string, unknown> | string | null;

const isPlainObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

export class ApiError extends Error {
  status: number;
  statusText: string;
  body: ApiErrorBody;

  constructor(message: string, status: number, statusText: string, body: ApiErrorBody = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.statusText = statusText;
    this.body = body;
  }

  static async fromResponse(response: Response): Promise<ApiError> {
    let body: ApiErrorBody = null;
    const contentType = response.headers.get('content-type') || '';
    try {
      if (contentType.includes('application/json')) {
        body = await response.json();
      } else {
        const text = await response.text();
        body = text || null;
      }
    } catch {
      body = null;
    }

    const detail =
      isPlainObject(body) && typeof body.detail === 'string'
        ? body.detail
        : typeof body === 'string'
          ? body
          : '';
    const message = detail || `Request failed: ${response.status} ${response.statusText}`;
    return new ApiError(message, response.status, response.statusText, body);
  }
}
