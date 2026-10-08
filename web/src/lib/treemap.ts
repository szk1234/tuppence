/** A squarified treemap layout (Bruls, Huizing & van Wijk): rectangles as close to square
 * as the values allow, biggest first. Positions are in the units of `box` (percent by default). */

export type Rect = { x: number; y: number; w: number; h: number }
export type Placed<T> = Rect & { item: T }

type Area<T> = { item: T; area: number }

function worst<T>(row: Area<T>[], side: number): number {
  const sum = row.reduce((s, r) => s + r.area, 0)
  const max = Math.max(...row.map((r) => r.area))
  const min = Math.min(...row.map((r) => r.area))
  return Math.max((side * side * max) / (sum * sum), (sum * sum) / (side * side * min))
}

function place<T>(row: Area<T>[], rect: Rect, out: Placed<T>[]): Rect {
  const sum = row.reduce((s, r) => s + r.area, 0)
  if (rect.w >= rect.h) {
    const w = sum / rect.h
    let y = rect.y
    for (const r of row) {
      const h = r.area / w
      out.push({ x: rect.x, y, w, h, item: r.item })
      y += h
    }
    return { x: rect.x + w, y: rect.y, w: rect.w - w, h: rect.h }
  }
  const h = sum / rect.w
  let x = rect.x
  for (const r of row) {
    const w = r.area / h
    out.push({ x, y: rect.y, w, h, item: r.item })
    x += w
  }
  return { x: rect.x, y: rect.y + h, w: rect.w, h: rect.h - h }
}

export function squarify<T>(
  items: readonly T[],
  value: (item: T) => number,
  box: Rect = { x: 0, y: 0, w: 100, h: 100 },
): Placed<T>[] {
  const data = items
    .map((item) => ({ item, v: Math.max(0, value(item)) }))
    .filter((d) => d.v > 0)
    .sort((a, b) => b.v - a.v)
  const total = data.reduce((s, d) => s + d.v, 0)
  if (!total || box.w <= 0 || box.h <= 0) return []
  const scale = (box.w * box.h) / total
  const areas: Area<T>[] = data.map((d) => ({ item: d.item, area: d.v * scale }))
  const out: Placed<T>[] = []
  let rect = { ...box }
  let row: Area<T>[] = []
  for (let i = 0; i < areas.length; ) {
    const side = Math.min(rect.w, rect.h)
    const next = areas[i]
    if (row.length === 0 || worst([...row, next], side) <= worst(row, side)) {
      row.push(next)
      i += 1
    } else {
      rect = place(row, rect, out)
      row = []
    }
  }
  if (row.length) place(row, rect, out)
  return out
}
