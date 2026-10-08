import { expect, it } from 'vitest'
import { squarify } from './treemap'

const area = (r: { w: number; h: number }) => r.w * r.h

it('fills the box exactly, biggest first, nothing outside', () => {
  const values = [6, 6, 4, 3, 2, 2, 1]
  const placed = squarify(values, (v) => v, { x: 0, y: 0, w: 600, h: 400 })
  expect(placed.map((p) => p.item)).toEqual(values)
  const total = placed.reduce((s, p) => s + area(p), 0)
  expect(total).toBeCloseTo(240000, 6)
  placed.forEach((p, i) => {
    expect(area(p)).toBeCloseTo((values[i] / 24) * 240000, 6)
    expect(p.x).toBeGreaterThanOrEqual(-1e-9)
    expect(p.y).toBeGreaterThanOrEqual(-1e-9)
    expect(p.x + p.w).toBeLessThanOrEqual(600 + 1e-9)
    expect(p.y + p.h).toBeLessThanOrEqual(400 + 1e-9)
  })
})

it('keeps tiles roughly square', () => {
  const placed = squarify([30, 25, 20, 10, 8, 7], (v) => v)
  for (const p of placed) expect(Math.max(p.w / p.h, p.h / p.w)).toBeLessThan(4)
})

it('drops empty and negative values and copes with nothing', () => {
  expect(squarify([0, -5, 3], (v) => v).map((p) => p.item)).toEqual([3])
  expect(squarify([], (v: number) => v)).toEqual([])
  expect(squarify([0], (v) => v)).toEqual([])
})
