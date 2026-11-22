export function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden className="logo">
      <rect width="32" height="32" rx="9" fill="var(--accent)" />
      <path d="M9 12.5a3.5 3.5 0 0 1 3.5-3.5h9a1.5 1.5 0 0 1 0 3h-9a.5.5 0 0 0 0 1H22a2 2 0 0 1 2 2V21a2 2 0 0 1-2 2H11a2 2 0 0 1-2-2v-8.5Z" fill="var(--accent-contrast)" />
      <circle cx="19.5" cy="18.25" r="1.5" fill="var(--accent)" />
    </svg>
  )
}
