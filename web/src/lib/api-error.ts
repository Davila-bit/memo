export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

let unauthorizedHandler: (() => void) | null = null;
/** The app shell registers this: any 401 sends Sarah back to the sign-in page. */
export function onUnauthorized(fn: (() => void) | null): void {
  unauthorizedHandler = fn;
}
export function notifyUnauthorized(): void {
  unauthorizedHandler?.();
}
