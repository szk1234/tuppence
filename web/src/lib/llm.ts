export type Preset = { id: string; label: string; api_style: string; base_url: string; kind: string; key_required: boolean }
export type Connection = {
  id: string; preset: string; name: string; api_style: string; base_url: string; is_local: boolean; has_key: boolean
  headers: Array<{ name: string; has_value: boolean }>; needs_notice: boolean; notice_acknowledged_at: string | null
  enabled: boolean; version: number
}
export type Detected = { preset: string; base_url: string; model_count: number }
/** A custom header row in a form. `saved` rows already have a value stored (write-only: never shown). */
export type HeaderRow = { name: string; value: string; saved: boolean; existing: boolean }
export const blankHeader = (): HeaderRow => ({ name: '', value: '', saved: false, existing: false })
