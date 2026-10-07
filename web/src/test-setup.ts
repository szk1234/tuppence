import '@testing-library/jest-dom/vitest'
import { configure } from '@testing-library/svelte'

// CI machines are slower than a laptop; give async queries (findBy*, waitFor) room
// so tests wait for the UI instead of racing it.
configure({ asyncUtilTimeout: 3000 })
