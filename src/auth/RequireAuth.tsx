export default function RequireAuth({ children }: { children: React.ReactNode }) {
  // Bypass authentication - always return children
  return <>{children}</>;
}
