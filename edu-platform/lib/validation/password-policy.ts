import { ApiError } from "@/lib/http/api-error";

/** Phase 6: min 8 chars, at least 3 of 4 classes (upper, lower, digit, special). */
export function assertPasswordPolicy(password: string): void {
  if (password.length < 8) {
    throw new ApiError(400, "VALIDATION_ERROR", "Password must be at least 8 characters");
  }
  const hasUpper = /[A-Z]/.test(password);
  const hasLower = /[a-z]/.test(password);
  const hasDigit = /\d/.test(password);
  const hasSpecial = /[^A-Za-z0-9]/.test(password);
  const classes = [hasUpper, hasLower, hasDigit, hasSpecial].filter(Boolean)
    .length;
  if (classes < 3) {
    throw new ApiError(
      400,
      "VALIDATION_ERROR",
      "Password must include at least 3 of: uppercase, lowercase, digit, special character",
    );
  }
}
