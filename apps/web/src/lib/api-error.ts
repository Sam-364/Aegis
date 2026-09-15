/**
 * The error every API call raises, shaped by the control plane's `application/problem+json`.
 *
 * It lives apart from the fetch client so that the demo fixture layer can raise the same error
 * without importing the client that calls it.
 */

export interface ProblemError {
  loc?: (string | number)[];
  msg?: string;
  type?: string;
}

export interface Problem {
  type?: string;
  title?: string;
  status?: number;
  detail?: unknown;
  request_id?: string;
  [key: string]: unknown;
}

export class ApiError extends Error {
  readonly status: number;
  readonly title: string;
  readonly detail: string | undefined;
  readonly type: string | undefined;
  readonly requestId: string | undefined;
  readonly errors: ProblemError[];
  readonly problem: Problem | undefined;
  readonly url: string;
  readonly method: string;
  readonly retryAfter: number | undefined;

  constructor(init: {
    status: number;
    title: string;
    detail?: string;
    type?: string;
    requestId?: string;
    errors?: ProblemError[];
    problem?: Problem;
    url: string;
    method: string;
    retryAfter?: number;
  }) {
    super(init.detail ? `${init.title}: ${init.detail}` : init.title);
    this.name = "ApiError";
    this.status = init.status;
    this.title = init.title;
    this.detail = init.detail;
    this.type = init.type;
    this.requestId = init.requestId;
    this.errors = init.errors ?? [];
    this.problem = init.problem;
    this.url = init.url;
    this.method = init.method;
    this.retryAfter = init.retryAfter;
  }

  get isNetwork(): boolean {
    return this.status === 0;
  }
}
