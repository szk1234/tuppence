export type Health = { status: string; version: string; mode: string }

export async function fetchHealth(fetchImpl: typeof fetch = fetch): Promise<Health> {
  const res = await fetchImpl('/health', { headers: { Accept: 'application/json' } })
  if (!res.ok) throw new Error(`Health check failed: ${res.status}`)
  return (await res.json()) as Health
}
