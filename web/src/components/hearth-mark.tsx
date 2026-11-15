/** The Memo mark: a rounded house with a warm glow in the doorway. Decorative. */
export function HearthMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 48 48" aria-hidden className={className}>
      <rect width="48" height="48" rx="14" fill="#1F2A2E" />
      <path d="M11 23.5 24 12l13 11.5V35a2 2 0 0 1-2 2H13a2 2 0 0 1-2-2V23.5Z" fill="#F6F1E9" />
      <path d="M20 37V29a4 4 0 0 1 8 0v8h-8Z" fill="#D98E2B" />
    </svg>
  );
}
