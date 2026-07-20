import { expect, test } from 'vitest'

test('provides the browser testing environment', () => {
  const element = document.createElement('div')
  document.body.append(element)

  expect(element).toBeInTheDocument()
  expect(indexedDB).toBeDefined()
})
